import asyncio
import logging
import json
import time
from typing import Dict, Any, List, Optional
from aiogram import Dispatcher, Bot, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.enums import ParseMode

import database as db
from publisher import send_post_to_target, build_final_text, build_post_keyboard

logger = logging.getLogger(__name__)

# Хранилище временного контента для альбомов и черновиков:
# media_groups: { f"{bot_id}_{group_id}": { "messages": [...], "task": Task, "user_id": int } }
media_groups: Dict[str, Dict[str, Any]] = {}

# draft_posts: { f"{bot_id}_{user_id}": { ... } }
draft_posts: Dict[str, Dict[str, Any]] = {}

# dm_targets: { f"{bot_id}_{admin_id}": target_user_id }
dm_targets: Dict[str, int] = {}


class PostStates(StatesGroup):
    waiting_for_custom_tag = State()
    waiting_for_button_title = State()
    waiting_for_button_url = State()
    waiting_for_schedule_time = State()
    waiting_for_dm_reply = State()


def get_client_dispatcher() -> Dispatcher:
    """Создаёт и конфигурирует общий диспетчер для всех клиентских ботов."""
    dp = Dispatcher()

    # ============== /start ==============
    @dp.message(CommandStart())
    async def cmd_start(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        await state.clear()
        user_id = message.from_user.id

        if await db.is_user_banned(bot_id, user_id):
            await message.answer("🚫 Вы заблокированы в этом боте.")
            return

        bot_info = await db.get_bot_by_id(bot_id)
        if not bot_info:
            return

        is_admin = await db.is_bot_admin(bot_id, user_id)
        channel_name = bot_info.get("channel_title") or bot_info.get("target_channel_id")

        if is_admin:
            tags = await db.get_bot_tags(bot_id)
            tag_list = " ".join([t["tag_name"] for t in tags]) if tags else "теги не настроены"

            await message.answer(
                f"👋 <b>Добро пожаловать в панель постинга!</b>\n\n"
                f"📢 <b>Канал:</b> {channel_name}\n"
                f"🏷 <b>Активные теги:</b> <code>{tag_list}</code>\n\n"
                f"📝 <b>Как опубликовать новость:</b>\n"
                f"Просто отправьте сюда текст, фото, видео или альбом (несколько фото/видео).\n"
                f"Бот сохранит всё форматирование (жирный, ссылки, спойлеры, цитаты), "
                f"предложит выбрать тег и покажет превью перед отправкой в канал!",
                parse_mode=ParseMode.HTML
            )
        else:
            mode = bot_info.get("mode", "direct")
            if mode == "suggest":
                await message.answer(
                    f"👋 <b>Добро пожаловать!</b>\n\n"
                    f"Здесь вы можете предложить новость для канала <b>{channel_name}</b>.\n"
                    f"Отправьте текст, фото или видео с описанием события. Редакция рассмотрит вашу новость перед публикацией!",
                    parse_mode=ParseMode.HTML
                )
            else:
                await message.answer(
                    f"🔒 Этот бот предназначен для публикации новостей администраторами канала <b>{channel_name}</b>.",
                    parse_mode=ParseMode.HTML
                )

    # ============== Прием медиа-группы (альбома) ==============
    @dp.message(F.media_group_id)
    async def handle_media_group(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        user_id = message.from_user.id
        if await db.is_user_banned(bot_id, user_id):
            return

        key = f"{bot_id}_{message.media_group_id}"
        if key not in media_groups:
            media_groups[key] = {"messages": [], "task": None, "user_id": user_id, "bot_id": bot_id}
        media_groups[key]["messages"].append(message)

        if media_groups[key]["task"]:
            media_groups[key]["task"].cancel()
        media_groups[key]["task"] = asyncio.create_task(finalize_media_group(key, bot, bot_id))

    async def finalize_media_group(key: str, bot: Bot, bot_id: int):
        await asyncio.sleep(1.0)
        group = media_groups.pop(key, None)
        if not group or not group["messages"]:
            return

        messages = group["messages"]
        first_msg = messages[0]
        user_id = group["user_id"]

        media_items = []
        for i, msg in enumerate(messages):
            fid = None
            mtype = None
            if msg.photo:
                fid = msg.photo[-1].file_id
                mtype = "photo"
            elif msg.video:
                fid = msg.video.file_id
                mtype = "video"
            elif msg.document:
                fid = msg.document.file_id
                mtype = "document"
            elif msg.animation:
                fid = msg.animation.file_id
                mtype = "animation"

            if fid and mtype:
                media_items.append({"file_id": fid, "type": mtype})

        caption_html = first_msg.html_text if first_msg.caption else ""

        post_data = {
            "media": media_items,
            "text_html": caption_html,
            "selected_tag": None,
            "custom_btn_title": None,
            "custom_btn_url": None,
            "user_id": user_id,
            "user_name": first_msg.from_user.full_name,
            "username": first_msg.from_user.username,
        }

        draft_key = f"{bot_id}_{user_id}"
        draft_posts[draft_key] = post_data
        await show_tag_selection(first_msg, bot, bot_id, user_id)

    # ============== Прием одиночных сообщений (текст / фото / видео) ==============
    @dp.message(~F.text.startswith("/"))
    async def handle_single_message(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        user_id = message.from_user.id
        if await db.is_user_banned(bot_id, user_id):
            await message.answer("🚫 Вы заблокированы.")
            return

        # Проверка прав: если не админ и режим direct — отказ
        is_admin = await db.is_bot_admin(bot_id, user_id)
        bot_info = await db.get_bot_by_id(bot_id)
        if not is_admin and bot_info.get("mode") == "direct":
            await message.answer("🔒 У вас нет прав для публикации новостей.")
            return

        # Проверка кулдауна
        settings = await db.get_bot_settings(bot_id)
        rem = await db.get_cooldown_remaining(bot_id, user_id, settings.get("cooldown_seconds", 5))
        if rem > 0 and not is_admin:
            await message.answer(f"⏳ Пожалуйста, подождите ещё <b>{rem} сек.</b> перед отправкой новости.")
            return

        media_type = None
        media_file_id = None

        if message.photo:
            media_type = "photo"
            media_file_id = message.photo[-1].file_id
        elif message.video:
            media_type = "video"
            media_file_id = message.video.file_id
        elif message.animation:
            media_type = "animation"
            media_file_id = message.animation.file_id
        elif message.document:
            media_type = "document"
            media_file_id = message.document.file_id
        elif message.voice:
            media_type = "voice"
            media_file_id = message.voice.file_id

        text_html = message.html_text or ""

        if not (text_html or media_file_id):
            await message.answer("❌ Пожалуйста, отправьте текст, фото, видео или документ.")
            return

        post_data = {
            "media_type": media_type,
            "media_file_id": media_file_id,
            "text_html": text_html,
            "selected_tag": None,
            "custom_btn_title": None,
            "custom_btn_url": None,
            "user_id": user_id,
            "user_name": message.from_user.full_name,
            "username": message.from_user.username,
        }

        draft_key = f"{bot_id}_{user_id}"
        draft_posts[draft_key] = post_data
        await show_tag_selection(message, bot, bot_id, user_id)

    # ============== Выбор тега ==============
    async def show_tag_selection(message: types.Message, bot: Bot, bot_id: int, user_id: int):
        tags = await db.get_bot_tags(bot_id)

        rows = []
        cur_row = []
        for t in tags:
            tag_name = t["tag_name"]
            cur_row.append(InlineKeyboardButton(text=tag_name, callback_data=f"set_tag:{t['id']}"))
            if len(cur_row) == 2:
                rows.append(cur_row)
                cur_row = []
        if cur_row:
            rows.append(cur_row)

        rows.append([
            InlineKeyboardButton(text="Без тега ❌", callback_data="set_tag:none"),
            InlineKeyboardButton(text="➕ Свой тег", callback_data="custom_tag")
        ])
        rows.append([InlineKeyboardButton(text="🚫 Отменить", callback_data="cancel_post")])

        kb = InlineKeyboardMarkup(inline_keyboard=rows)
        await message.answer("🏷 <b>Выберите тег (рубрику) для новости:</b>", reply_markup=kb, parse_mode=ParseMode.HTML)

    @dp.callback_query(F.data.startswith("set_tag:"))
    async def process_tag_choice(callback: types.CallbackQuery, bot: Bot, bot_id: int, state: FSMContext):
        tag_arg = callback.data.split(":")[1]
        user_id = callback.from_user.id
        draft_key = f"{bot_id}_{user_id}"
        post_data = draft_posts.get(draft_key)

        if not post_data:
            await callback.answer("Черновик устарел или не найден.", show_alert=True)
            return

        if tag_arg == "none":
            post_data["selected_tag"] = None
        else:
            try:
                tag_id = int(tag_arg)
                tags = await db.get_bot_tags(bot_id)
                found = next((t["tag_name"] for t in tags if t["id"] == tag_id), None)
                post_data["selected_tag"] = found
            except ValueError:
                post_data["selected_tag"] = None

        await callback.answer()
        await show_post_preview(callback.message, bot, bot_id, user_id)

    @dp.callback_query(F.data == "custom_tag")
    async def process_custom_tag(callback: types.CallbackQuery, state: FSMContext):
        await state.set_state(PostStates.waiting_for_custom_tag)
        await callback.message.answer("✍️ Напишите название тега (например, <code>#инсайд</code> или <code>обновление</code>):", parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.message(PostStates.waiting_for_custom_tag)
    async def receive_custom_tag(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        user_id = message.from_user.id
        draft_key = f"{bot_id}_{user_id}"
        post_data = draft_posts.get(draft_key)
        if not post_data:
            await message.answer("Черновик не найден. Отправьте новость заново.")
            await state.clear()
            return

        tag = message.text.strip()
        if not tag.startswith("#"):
            tag = f"#{tag}"

        post_data["selected_tag"] = tag
        await state.clear()
        await show_post_preview(message, bot, bot_id, user_id)

    # ============== Превью публикации ==============
    async def show_post_preview(message: types.Message, bot: Bot, bot_id: int, user_id: int):
        draft_key = f"{bot_id}_{user_id}"
        post_data = draft_posts.get(draft_key)
        if not post_data:
            return

        settings = await db.get_bot_settings(bot_id)
        is_admin = await db.is_bot_admin(bot_id, user_id)

        # Отправляем предварительный просмотр поста в ЛС
        await message.answer("👁 <b>Предварительный просмотр публикации:</b>\n<i>(именно так пост будет выглядеть в канале)</i>", parse_mode=ParseMode.HTML)

        post_kb = build_post_keyboard(settings, post_data.get("custom_btn_title"), post_data.get("custom_btn_url"))
        await send_post_to_target(bot, user_id, post_data, settings, reply_markup=post_kb)

        # Панель управления постом
        ctrl_kb_rows = []
        if is_admin:
            ctrl_kb_rows.append([
                InlineKeyboardButton(text="🚀 Опубликовать в канал", callback_data="pub_now"),
                InlineKeyboardButton(text="⏰ Отложить пост", callback_data="pub_schedule")
            ])
        else:
            ctrl_kb_rows.append([
                InlineKeyboardButton(text="📩 Отправить на модерацию", callback_data="pub_suggest")
            ])

        ctrl_kb_rows.append([
            InlineKeyboardButton(text="🔗 Добавить кнопку", callback_data="add_btn"),
            InlineKeyboardButton(text="🏷 Сменить тег", callback_data="change_tag")
        ])
        ctrl_kb_rows.append([
            InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_post")
        ])

        await message.answer("⚙️ <b>Выберите действие:</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=ctrl_kb_rows), parse_mode=ParseMode.HTML)

    @dp.callback_query(F.data == "change_tag")
    async def change_tag_callback(callback: types.CallbackQuery, bot: Bot, bot_id: int):
        await show_tag_selection(callback.message, bot, bot_id, callback.from_user.id)
        await callback.answer()

    @dp.callback_query(F.data == "cancel_post")
    async def cancel_post_callback(callback: types.CallbackQuery, bot: Bot, bot_id: int, state: FSMContext):
        draft_key = f"{bot_id}_{callback.from_user.id}"
        draft_posts.pop(draft_key, None)
        await state.clear()
        try:
            await callback.message.edit_text("❌ Публикация отменена. Можете отправить новый пост.")
        except TelegramBadRequest:
            pass
        await callback.answer()

    # ============== Добавление инлайн-кнопки ==============
    @dp.callback_query(F.data == "add_btn")
    async def add_btn_callback(callback: types.CallbackQuery, state: FSMContext):
        await state.set_state(PostStates.waiting_for_button_title)
        await callback.message.answer("📝 Введите текст для кнопки (например: <code>Читать далее</code>):", parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.message(PostStates.waiting_for_button_title)
    async def receive_btn_title(message: types.Message, state: FSMContext):
        await state.update_data(btn_title=message.text.strip())
        await state.set_state(PostStates.waiting_for_button_url)
        await message.answer("🌐 Введите ссылку (URL) для кнопки (например: <code>https://t.me/channel</code>):", parse_mode=ParseMode.HTML)

    @dp.message(PostStates.waiting_for_button_url)
    async def receive_btn_url(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        url = message.text.strip()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
            await message.answer("⚠️ Некорректная ссылка! Ссылка должна начинаться с https:// или tg://. Попробуйте снова:")
            return

        data = await state.get_data()
        title = data.get("btn_title", "Ссылка")
        await state.clear()

        draft_key = f"{bot_id}_{message.from_user.id}"
        post_data = draft_posts.get(draft_key)
        if post_data:
            post_data["custom_btn_title"] = title
            post_data["custom_btn_url"] = url
            await message.answer(f"✅ Кнопка <b>[{title}]</b> добавлена!", parse_mode=ParseMode.HTML)
            await show_post_preview(message, bot, bot_id, message.from_user.id)
        else:
            await message.answer("Черновик не найден.")

    # ============== Публикация СЕЙЧАС (Админ) ==============
    @dp.callback_query(F.data == "pub_now")
    async def publish_now_callback(callback: types.CallbackQuery, bot: Bot, bot_id: int, state: FSMContext):
        user_id = callback.from_user.id
        draft_key = f"{bot_id}_{user_id}"
        post_data = draft_posts.pop(draft_key, None)

        if not post_data:
            await callback.answer("Черновик не найден.", show_alert=True)
            return

        bot_info = await db.get_bot_by_id(bot_id)
        settings = await db.get_bot_settings(bot_id)
        channel_id = bot_info["target_channel_id"]

        try:
            post_kb = build_post_keyboard(settings, post_data.get("custom_btn_title"), post_data.get("custom_btn_url"))
            await send_post_to_target(bot, channel_id, post_data, settings, reply_markup=post_kb)
            await db.update_cooldown(bot_id, user_id)
            await callback.message.edit_text("✅ <b>Новость успешно опубликована в канал!</b>", parse_mode=ParseMode.HTML)
            await callback.answer("Опубликовано!")
        except Exception as e:
            logger.error(f"Error publishing to {channel_id}: {e}")
            await callback.message.answer(f"❌ <b>Ошибка при публикации в канал:</b>\n<code>{e}</code>\n\nУбедитесь, что бот добавлен в администраторы канала с правом публикации сообщений!", parse_mode=ParseMode.HTML)
            await callback.answer()

    # ============== Отложенный постинг ==============
    @dp.callback_query(F.data == "pub_schedule")
    async def schedule_callback(callback: types.CallbackQuery, state: FSMContext):
        await state.set_state(PostStates.waiting_for_schedule_time)
        await callback.message.answer(
            "⏰ <b>Через сколько минут опубликовать новость?</b>\n\n"
            "Введите число минут (например: <code>15</code>, <code>60</code>, <code>180</code>):",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(PostStates.waiting_for_schedule_time)
    async def receive_schedule_time(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        try:
            minutes = int(message.text.strip())
            if minutes < 1:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите корректное число минут (целое положительное число).")
            return

        await state.clear()
        draft_key = f"{bot_id}_{message.from_user.id}"
        post_data = draft_posts.pop(draft_key, None)
        if not post_data:
            await message.answer("Черновик не найден.")
            return

        schedule_time = int(time.time()) + minutes * 60
        await db.add_scheduled_post(bot_id, message.from_user.id, post_data, schedule_time)

        await message.answer(f"✅ <b>Новость поставлена в очередь!</b>\nОна будет опубликована через {minutes} мин.", parse_mode=ParseMode.HTML)

    # ============== Предложка (модерация) ==============
    @dp.callback_query(F.data == "pub_suggest")
    async def suggest_callback(callback: types.CallbackQuery, bot: Bot, bot_id: int):
        user_id = callback.from_user.id
        draft_key = f"{bot_id}_{user_id}"
        post_data = draft_posts.pop(draft_key, None)

        if not post_data:
            await callback.answer("Черновик не найден.", show_alert=True)
            return

        bot_info = await db.get_bot_by_id(bot_id)
        owner_id = bot_info["owner_id"]
        settings = await db.get_bot_settings(bot_id)

        # Отправляем владельцу бота на согласование
        author_info = f"👤 <b>От автора:</b> {post_data['user_name']} (@{post_data['username'] or 'id' + str(user_id)})"
        try:
            await bot.send_message(owner_id, f"📥 <b>Новая предложенная новость!</b>\n{author_info}", parse_mode=ParseMode.HTML)
            post_kb = build_post_keyboard(settings, post_data.get("custom_btn_title"), post_data.get("custom_btn_url"))
            await send_post_to_target(bot, owner_id, post_data, settings, reply_markup=post_kb)

            # Сохраняем пост для кнопки одобрения
            temp_suggest_key = f"sug_{bot_id}_{user_id}_{int(time.time())}"
            draft_posts[temp_suggest_key] = post_data

            mod_kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить и в канал", callback_data=f"mod_appr:{temp_suggest_key}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"mod_rej:{temp_suggest_key}:{user_id}")
                ],
                [
                    InlineKeyboardButton(text="✉️ Написать автору", callback_data=f"mod_dm:{user_id}"),
                    InlineKeyboardButton(text="🚫 Забанить", callback_data=f"mod_ban:{user_id}")
                ]
            ])
            await bot.send_message(owner_id, "⚙️ <b>Действия модератора:</b>", reply_markup=mod_kb, parse_mode=ParseMode.HTML)

            await callback.message.edit_text("✅ <b>Ваша новость отправлена редакторам канала!</b>\nСпасибо за ваш вклад.", parse_mode=ParseMode.HTML)
            await db.update_cooldown(bot_id, user_id)
        except Exception as e:
            logger.error(f"Error forwarding suggestion: {e}")
            await callback.message.answer(f"❌ Не удалось отправить новость: {e}")

        await callback.answer()

    # ============== Кнопки модератора ==============
    @dp.callback_query(F.data.startswith("mod_appr:"))
    async def mod_approve(callback: types.CallbackQuery, bot: Bot, bot_id: int):
        sug_key = callback.data.split(":", 1)[1]
        post_data = draft_posts.pop(sug_key, None)

        if not post_data:
            await callback.answer("Данные новости устарели.", show_alert=True)
            return

        bot_info = await db.get_bot_by_id(bot_id)
        settings = await db.get_bot_settings(bot_id)
        channel_id = bot_info["target_channel_id"]

        try:
            post_kb = build_post_keyboard(settings, post_data.get("custom_btn_title"), post_data.get("custom_btn_url"))
            await send_post_to_target(bot, channel_id, post_data, settings, reply_markup=post_kb)
            await callback.message.edit_text(f"✅ <b>Новость одобрена и опубликована в канал {channel_id}!</b>", parse_mode=ParseMode.HTML)

            # Уведомляем автора
            author_id = post_data.get("user_id")
            if author_id:
                try:
                    await bot.send_message(author_id, "🎉 <b>Ваша предложенная новость была опубликована в канале!</b>", parse_mode=ParseMode.HTML)
                except Exception:
                    pass
        except Exception as e:
            await callback.message.answer(f"❌ Ошибка публикации: {e}")

        await callback.answer()

    @dp.callback_query(F.data.startswith("mod_rej:"))
    async def mod_reject(callback: types.CallbackQuery, bot: Bot, bot_id: int):
        _, sug_key, author_id_str = callback.data.split(":")
        draft_posts.pop(sug_key, None)
        author_id = int(author_id_str)

        try:
            await bot.send_message(author_id, "ℹ️ К сожалению, ваша предложенная новость была отклонена редакцией.")
        except Exception:
            pass

        await callback.message.edit_text("❌ Новость отклонена.")
        await callback.answer()

    @dp.callback_query(F.data.startswith("mod_ban:"))
    async def mod_ban_user(callback: types.CallbackQuery, bot_id: int):
        target_id = int(callback.data.split(":")[1])
        await db.ban_user(bot_id, target_id, "Спам/неприемлемый контент", callback.from_user.id)
        await callback.message.edit_text(f"🚫 Пользователь <code>{target_id}</code> забанен.", parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data.startswith("mod_dm:"))
    async def mod_start_dm(callback: types.CallbackQuery, bot_id: int, state: FSMContext):
        target_id = int(callback.data.split(":")[1])
        dm_targets[f"{bot_id}_{callback.from_user.id}"] = target_id
        await state.set_state(PostStates.waiting_for_dm_reply)
        await callback.message.answer("✍️ Напишите ответ автору новости в чат (он получит это в ЛС):")
        await callback.answer()

    @dp.message(PostStates.waiting_for_dm_reply)
    async def mod_send_dm(message: types.Message, bot: Bot, bot_id: int, state: FSMContext):
        target_id = dm_targets.pop(f"{bot_id}_{message.from_user.id}", None)
        await state.clear()
        if not target_id:
            return

        try:
            await bot.send_message(target_id, f"✉️ <b>Сообщение от редакции:</b>\n\n{message.text}", parse_mode=ParseMode.HTML)
            await message.answer("✅ Сообщение успешно доставлено автору.")
        except Exception as e:
            await message.answer(f"❌ Не удалось доставить сообщение автору: {e}")

    # ============== Админ-команды в клиентском боте ==============
    @dp.message(Command("ban"))
    async def cmd_ban_client(message: types.Message, bot_id: int):
        if not await db.is_bot_admin(bot_id, message.from_user.id):
            return
        args = message.text.split(maxsplit=2)
        if len(args) < 2:
            await message.answer("Использование: /ban <user_id> [причина]")
            return
        try:
            target = int(args[1])
            reason = args[2] if len(args) > 2 else "нарушение правил"
            await db.ban_user(bot_id, target, reason, message.from_user.id)
            await message.answer(f"🚫 Пользователь <code>{target}</code> забанен.")
        except ValueError:
            await message.answer("❌ ID должен быть числом.")

    @dp.message(Command("unban"))
    async def cmd_unban_client(message: types.Message, bot_id: int):
        if not await db.is_bot_admin(bot_id, message.from_user.id):
            return
        args = message.text.split(maxsplit=1)
        if len(args) < 2:
            await message.answer("Использование: /unban <user_id>")
            return
        try:
            target = int(args[1])
            if await db.unban_user(bot_id, target):
                await message.answer(f"✅ Пользователь <code>{target}</code> разбанен.")
            else:
                await message.answer("Пользователь не был в бане.")
        except ValueError:
            await message.answer("❌ ID должен быть числом.")

    return dp
