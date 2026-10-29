import asyncio
import logging
from typing import Optional
from aiogram import Dispatcher, Bot, types, F
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.exceptions import TelegramBadRequest, TelegramUnauthorizedError
from aiogram.enums import ParseMode

import database as db
from manager import manager

logger = logging.getLogger(__name__)


class CreateBotStates(StatesGroup):
    waiting_for_token = State()
    waiting_for_channel = State()
    waiting_for_mode = State()


class SettingsStates(StatesGroup):
    waiting_for_watermark = State()
    waiting_for_header = State()
    waiting_for_footer = State()
    waiting_for_btn_title = State()
    waiting_for_btn_url = State()
    waiting_for_cooldown = State()
    waiting_for_new_tag = State()
    waiting_for_new_admin_id = State()
    waiting_for_new_channel = State()


def get_master_dispatcher() -> Dispatcher:
    """Создаёт диспетчер для главного бота-конструктора."""
    dp = Dispatcher()

    # ============== /start ==============
    @dp.message(CommandStart())
    async def cmd_start(message: types.Message, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🤖 Мои боты", callback_data="my_bots"),
                InlineKeyboardButton(text="➕ Создать бота", callback_data="create_bot"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика хостинга", callback_data="host_stats"),
            ]
        ])
        await message.answer(
            "⚡️ <b>Конструктор новостных ботов (Host Manager)</b>\n\n"
            "Этот бот позволяет создавать, тонко настраивать и хостить собственных ботов "
            "для публикации новостей в Telegram-каналы со всеми тегами, форматированием, "
            "медиа-альбомами и кнопками прямо с этого сервера/ПК!\n\n"
            "Выберите нужное действие ниже:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    # ============== Список ботов пользователя ==============
    @dp.callback_query(F.data == "my_bots")
    async def show_my_bots(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        user_bots = await db.get_user_bots(callback.from_user.id)

        if not user_bots:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="➕ Создать первого бота", callback_data="create_bot")],
                [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")]
            ])
            await callback.message.edit_text(
                "ℹ️ У вас пока нет созданных ботов.\n\nНажмите кнопку ниже, чтобы создать бота!",
                reply_markup=kb,
                parse_mode=ParseMode.HTML
            )
            await callback.answer()
            return

        rows = []
        for b in user_bots:
            is_run = manager.is_bot_running(b["id"])
            indicator = "🟢" if is_run else "🔴"
            status_text = "Работает" if is_run else "Остановлен"
            uname = b["bot_username"] or "без username"
            rows.append([
                InlineKeyboardButton(
                    text=f"{indicator} @{uname} ({status_text})",
                    callback_data=f"bot_manage:{b['id']}"
                )
            ])

        rows.append([InlineKeyboardButton(text="➕ Создать ещё бота", callback_data="create_bot")])
        rows.append([InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")])

        await callback.message.edit_text(
            "🤖 <b>Ваши боты на хостинге:</b>\n"
            "Нажмите на бота, чтобы открыть меню управления и тонкой настройки:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    # ============== Статистика хостинга ==============
    @dp.callback_query(F.data == "host_stats")
    async def show_host_stats(callback: types.CallbackQuery):
        stats = await db.get_system_stats()
        text = (
            "📊 <b>Статистика хостинга ботов:</b>\n\n"
            f"🤖 Всего создано ботов: <b>{stats['total_bots']}</b>\n"
            f"🟢 Активных сейчас: <b>{len(manager._tasks)}</b>\n"
            f"👥 Всего администраторов: <b>{stats['total_admins']}</b>\n"
            f"⏰ Постов в очереди планировщика: <b>{stats['total_scheduled']}</b>\n\n"
            "💻 <i>Сервер и бот-хостинг функционируют в штатном режиме.</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Обновить", callback_data="host_stats")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")]
        ])
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data == "main_menu")
    async def main_menu_callback(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="🤖 Мои боты", callback_data="my_bots"),
                InlineKeyboardButton(text="➕ Создать бота", callback_data="create_bot"),
            ],
            [
                InlineKeyboardButton(text="📊 Статистика хостинга", callback_data="host_stats"),
            ]
        ])
        await callback.message.edit_text(
            "⚡️ <b>Конструктор новостных ботов (Host Manager)</b>\n\n"
            "Выберите нужное действие ниже:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    # ============== Мастер создания бота (Wizard) ==============
    @dp.callback_query(F.data == "create_bot")
    async def start_create_bot(callback: types.CallbackQuery, state: FSMContext):
        await state.set_state(CreateBotStates.waiting_for_token)
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="main_menu")]
        ])
        await callback.message.edit_text(
            "📝 <b>Шаг 1 из 3: Токен бота</b>\n\n"
            "1. Перейдите в @BotFather и создайте нового бота командой <code>/newbot</code>\n"
            "2. Скопируйте полученный API токен (вид: <code>123456789:ABCdefGHIjk...</code>)\n"
            "3. Отправьте токен ответным сообщением сюда:",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(CreateBotStates.waiting_for_token)
    async def process_bot_token(message: types.Message, state: FSMContext):
        token = message.text.strip()

        # Проверяем в базе, нет ли уже такого токена
        existing = await db.get_bot_by_token(token)
        if existing:
            await message.answer("⚠️ Бот с таким токеном уже зарегистрирован в системе! Введите другой токен или отмените действие /start.")
            return

        # Проверяем валидность токена через Telegram API
        try:
            temp_bot = Bot(token=token)
            me = await temp_bot.get_me()
            await temp_bot.session.close()
        except TelegramUnauthorizedError:
            await message.answer("❌ <b>Неверный токен бота!</b>\nTelegram вернул ошибку авторизации. Проверьте правильность токена и попробуйте снова:")
            return
        except Exception as e:
            await message.answer(f"❌ Ошибка соединения с Telegram: {e}\nПопробуйте ещё раз:")
            return

        await state.update_data(
            token=token,
            bot_username=me.username,
            bot_name=me.first_name
        )
        await state.set_state(CreateBotStates.waiting_for_channel)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отменить", callback_data="main_menu")]
        ])
        await message.answer(
            f"✅ Токен принят! Бот: <b>@{me.username}</b> ({me.first_name})\n\n"
            f"📢 <b>Шаг 2 из 3: Целевой канал</b>\n\n"
            f"1. Добавьте вашего бота <b>@{me.username}</b> в ваш Telegram-канал как <b>Администратора</b> с правом публикации сообщений.\n"
            f"2. Пришлите сюда <b>@username</b> канала (например <code>@my_channel</code>) "
            f"или числовой <b>ID</b> канала (например <code>-1001234567890</code>):",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    @dp.message(CreateBotStates.waiting_for_channel)
    async def process_bot_channel(message: types.Message, state: FSMContext):
        channel_input = message.text.strip()
        data = await state.get_data()
        token = data.get("token")

        channel_title = channel_input
        # Пытаемся получить информацию о канале через бота
        try:
            temp_bot = Bot(token=token)
            chat = await temp_bot.get_chat(channel_input)
            channel_title = chat.title or channel_input
            await temp_bot.session.close()
        except Exception as e:
            logger.info(f"Не удалось получить chat info: {e}")

        await state.update_data(
            target_channel_id=channel_input,
            channel_title=channel_title
        )
        await state.set_state(CreateBotStates.waiting_for_mode)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="📢 Прямой постинг (только админы)", callback_data="mode:direct")
            ],
            [
                InlineKeyboardButton(text="📬 Предложка (подписчики + модерация)", callback_data="mode:suggest")
            ],
            [
                InlineKeyboardButton(text="❌ Отменить", callback_data="main_menu")
            ]
        ])
        await message.answer(
            f"📢 Канал: <b>{channel_title}</b> (<code>{channel_input}</code>)\n\n"
            f"⚙️ <b>Шаг 3 из 3: Режим работы бота</b>\n\n"
            f"• <b>Прямой постинг</b>: бот доступен только вам и добавленным вами редакторам. Вы присылаете новость, бот оформляет её, выбираете тег и публикуете.\n"
            f"• <b>Предложка</b>: обычные читатели тоже могут присылать новости, а вы модерируете их (одобрить / отклонить / ответить) перед выходом в канал.",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )

    @dp.callback_query(F.data.startswith("mode:"))
    async def finish_create_bot(callback: types.CallbackQuery, state: FSMContext):
        mode = callback.data.split(":")[1]
        data = await state.get_data()
        await state.clear()

        token = data["token"]
        bot_username = data["bot_username"]
        bot_name = data["bot_name"]
        target_channel_id = data["target_channel_id"]
        channel_title = data["channel_title"]
        owner_id = callback.from_user.id

        bot_id = await db.create_bot(
            owner_id=owner_id,
            token=token,
            bot_username=bot_username,
            bot_name=bot_name,
            target_channel_id=target_channel_id,
            channel_title=channel_title,
            mode=mode
        )

        # Запускаем бота на хостинге
        ok, msg = await manager.start_bot(bot_id)

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ Перейти к настройкам бота", callback_data=f"bot_manage:{bot_id}")],
            [InlineKeyboardButton(text="🤖 Список ботов", callback_data="my_bots")]
        ])

        status_text = "🟢 Бот успешно запущен на хостинге!" if ok else f"⚠️ Бот создан, но запуск вернул: {msg}"
        await callback.message.edit_text(
            f"🎉 <b>Бот @{bot_username} готов к работе!</b>\n\n"
            f"📢 Канал: <b>{channel_title}</b>\n"
            f"Режим: <b>{'Прямой постинг' if mode == 'direct' else 'Предложка новостей'}</b>\n"
            f"Статус: {status_text}\n\n"
            f"Теперь вы можете открыть @{bot_username}, отправить в него текст или альбом с фото/видео, "
            f"выбрать тег и опубликовать в канал!",
            reply_markup=kb,
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    # ============== Панель управления конкретным ботом ==============
    @dp.callback_query(F.data.startswith("bot_manage:"))
    async def manage_bot_menu(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        bot_id = int(callback.data.split(":")[1])
        bot_info = await db.get_bot_by_id(bot_id)

        if not bot_info:
            await callback.answer("Бот не найден.", show_alert=True)
            return

        is_run = manager.is_bot_running(bot_id)
        indicator = "🟢 Работает" if is_run else "🔴 Остановлен"
        toggle_btn_text = "⏸ Остановить" if is_run else "▶️ Запустить"
        toggle_data = f"bot_stop:{bot_id}" if is_run else f"bot_start:{bot_id}"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="⚙️ Настройки публикации", callback_data=f"bot_settings:{bot_id}"),
                InlineKeyboardButton(text="🏷 Управление тегами", callback_data=f"bot_tags:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="👥 Редакторы (админы)", callback_data=f"bot_admins:{bot_id}"),
                InlineKeyboardButton(text="📢 Сменить канал", callback_data=f"bot_chnl:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="🔄 Сменить режим (прямой/предложка)", callback_data=f"bot_togglemode:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text=toggle_btn_text, callback_data=toggle_data),
                InlineKeyboardButton(text="🔄 Перезапустить", callback_data=f"bot_restart:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="🗑 Удалить бота", callback_data=f"bot_delete_confirm:{bot_id}"),
                InlineKeyboardButton(text="◀️ Назад к списку", callback_data="my_bots"),
            ]
        ])

        mode_str = "Прямой постинг (авторы)" if bot_info["mode"] == "direct" else "Предложка новостей (модерация)"
        text = (
            f"🤖 <b>Панель бота @{bot_info['bot_username']}</b>\n"
            f"🆔 ID в системе: <code>{bot_id}</code>\n"
            f"Статус: <b>{indicator}</b>\n"
            f"Канал: <b>{bot_info['channel_title']}</b> (<code>{bot_info['target_channel_id']}</code>)\n"
            f"Режим: <b>{mode_str}</b>\n\n"
            f"Выберите нужный раздел настройки:"
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    # ============== Старт / Стоп / Рестарт бота ==============
    @dp.callback_query(F.data.startswith("bot_start:"))
    async def action_bot_start(callback: types.CallbackQuery):
        bot_id = int(callback.data.split(":")[1])
        ok, msg = await manager.start_bot(bot_id)
        await callback.answer(msg, show_alert=not ok)
        await manage_bot_menu(callback, None)

    @dp.callback_query(F.data.startswith("bot_stop:"))
    async def action_bot_stop(callback: types.CallbackQuery):
        bot_id = int(callback.data.split(":")[1])
        await manager.stop_bot(bot_id)
        await callback.answer("Бот остановлен.")
        await manage_bot_menu(callback, None)

    @dp.callback_query(F.data.startswith("bot_restart:"))
    async def action_bot_restart(callback: types.CallbackQuery):
        bot_id = int(callback.data.split(":")[1])
        ok, msg = await manager.restart_bot(bot_id)
        await callback.answer(msg, show_alert=not ok)
        await manage_bot_menu(callback, None)

    @dp.callback_query(F.data.startswith("bot_togglemode:"))
    async def action_bot_togglemode(callback: types.CallbackQuery):
        bot_id = int(callback.data.split(":")[1])
        bot_info = await db.get_bot_by_id(bot_id)
        new_mode = "suggest" if bot_info["mode"] == "direct" else "direct"
        await db.update_bot_mode(bot_id, new_mode)
        await callback.answer(f"Режим переключен на: {'Предложка' if new_mode == 'suggest' else 'Прямой'}")
        await manage_bot_menu(callback, None)

    # ============== Удаление бота ==============
    @dp.callback_query(F.data.startswith("bot_delete_confirm:"))
    async def action_bot_delete_confirm(callback: types.CallbackQuery):
        bot_id = int(callback.data.split(":")[1])
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="💥 Да, удалить навсегда", callback_data=f"bot_delete_do:{bot_id}"),
                InlineKeyboardButton(text="Отмена", callback_data=f"bot_manage:{bot_id}")
            ]
        ])
        await callback.message.edit_text("⚠️ <b>Вы уверены, что хотите удалить этого бота с хостинга?</b>\nВсе его настройки и теги будут стёрты.", reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data.startswith("bot_delete_do:"))
    async def action_bot_delete(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await manager.stop_bot(bot_id)
        await db.delete_bot(bot_id)
        await callback.answer("Бот удалён.")
        await show_my_bots(callback, state)

    # ============== Настройки постинга (Watermark, кнопки, режимы) ==============
    @dp.callback_query(F.data.startswith("bot_settings:"))
    async def view_bot_settings(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        bot_id = int(callback.data.split(":")[1])
        s = await db.get_bot_settings(bot_id)

        wm = s.get("watermark") or "не задан"
        hdr = s.get("header_format") or "не задана"
        ftr = s.get("footer_format") or "не задан"
        btn_str = f"[{s.get('button_title')}] -> {s.get('button_url')}" if s.get("button_title") else "нет кнопки"
        preview_stat = "🟢 Включен" if s.get("link_preview") else "🔴 Выключен"
        silent_stat = "🔔 Со звуком" if not s.get("silent_mode") else "🔕 Беззвучно"
        protect_stat = "🔓 Разрешено" if not s.get("protect_content") else "🔒 Запрещено"

        kb = InlineKeyboardMarkup(inline_keyboard=[
            [
                InlineKeyboardButton(text="✏️ Водяной знак / Подпись", callback_data=f"set_wm:{bot_id}"),
                InlineKeyboardButton(text="✏️ Шапка новости", callback_data=f"set_hdr:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="✏️ Подвал новости", callback_data=f"set_ftr:{bot_id}"),
                InlineKeyboardButton(text="🔘 Постоянная кнопка", callback_data=f"set_btn:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text=f"Превью ссылок: {preview_stat}", callback_data=f"toggle_prev:{bot_id}"),
                InlineKeyboardButton(text=f"{silent_stat}", callback_data=f"toggle_silent:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text=f"Копирование: {protect_stat}", callback_data=f"toggle_protect:{bot_id}"),
                InlineKeyboardButton(text=f"⏱ Антифлуд: {s.get('cooldown_seconds')}с", callback_data=f"set_cd:{bot_id}"),
            ],
            [
                InlineKeyboardButton(text="◀️ Назад в меню бота", callback_data=f"bot_manage:{bot_id}")
            ]
        ])

        text = (
            f"⚙️ <b>Тонкая настройка публикаций</b> (Бот #{bot_id})\n\n"
            f"🏷 <b>Шапка:</b> <code>{hdr}</code>\n"
            f"✍️ <b>Водяной знак:</b> <code>{wm}</code>\n"
            f"📄 <b>Подвал:</b> <code>{ftr}</code>\n"
            f"🔘 <b>Кнопка по умолчанию:</b> <code>{btn_str}</code>\n"
            f"🌐 <b>Предпросмотр ссылок:</b> {preview_stat}\n"
            f"🔔 <b>Уведомление в канале:</b> {silent_stat}\n"
            f"🔒 <b>Защита от копирования:</b> {protect_stat}\n"
            f"⏱ <b>Кулдаун между постами:</b> {s.get('cooldown_seconds')} сек.\n\n"
            f"<i>Подсказка: в шапке, подвале и водяном знаке можно использовать переменную <code>{{tag}}</code> — она автоматически заменится на выбранный тег!</i>"
        )
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
        await callback.answer()

    # Тогглы настроек
    @dp.callback_query(F.data.startswith("toggle_prev:"))
    async def toggle_preview(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        s = await db.get_bot_settings(bot_id)
        new_val = 0 if s.get("link_preview") else 1
        await db.update_bot_setting(bot_id, "link_preview", new_val)
        await view_bot_settings(callback, state)

    @dp.callback_query(F.data.startswith("toggle_silent:"))
    async def toggle_silent(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        s = await db.get_bot_settings(bot_id)
        new_val = 0 if s.get("silent_mode") else 1
        await db.update_bot_setting(bot_id, "silent_mode", new_val)
        await view_bot_settings(callback, state)

    @dp.callback_query(F.data.startswith("toggle_protect:"))
    async def toggle_protect(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        s = await db.get_bot_settings(bot_id)
        new_val = 0 if s.get("protect_content") else 1
        await db.update_bot_setting(bot_id, "protect_content", new_val)
        await view_bot_settings(callback, state)

    # Редактирование водяного знака
    @dp.callback_query(F.data.startswith("set_wm:"))
    async def edit_wm_start(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_watermark)
        await callback.message.answer(
            "✍️ Отправьте новый текст водяного знака/подписи для постов.\n\n"
            "Пример:\n<code>#новость | @my_cool_channel</code>\n\n"
            "Или отправьте <code>-</code> (дефис), чтобы убрать водяной знак.",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_watermark)
    async def edit_wm_finish(message: types.Message, state: FSMContext):
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        val = "" if message.text.strip() == "-" else message.text.strip()
        await db.update_bot_setting(bot_id, "watermark", val)
        await state.clear()
        await message.answer("✅ Водяной знак сохранён!")

    # Редактирование шапки
    @dp.callback_query(F.data.startswith("set_hdr:"))
    async def edit_hdr_start(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_header)
        await callback.message.answer(
            "✍️ Отправьте шаблон шапки новости.\n\n"
            "Пример:\n<code>⚡️ СРОЧНО | {tag}</code>\n\n"
            "Или <code>-</code>, чтобы отключить шапку.",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_header)
    async def edit_hdr_finish(message: types.Message, state: FSMContext):
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        val = "" if message.text.strip() == "-" else message.text.strip()
        await db.update_bot_setting(bot_id, "header_format", val)
        await state.clear()
        await message.answer("✅ Шапка новости сохранена!")

    # Редактирование подвала
    @dp.callback_query(F.data.startswith("set_ftr:"))
    async def edit_ftr_start(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_footer)
        await callback.message.answer(
            "✍️ Отправьте шаблон подвала новости.\n\n"
            "Пример:\n<code>📌 Читайте подробнее в нашем боте: @bot</code>\n\n"
            "Или <code>-</code>, чтобы отключить подвал.",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_footer)
    async def edit_ftr_finish(message: types.Message, state: FSMContext):
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        val = "" if message.text.strip() == "-" else message.text.strip()
        await db.update_bot_setting(bot_id, "footer_format", val)
        await state.clear()
        await message.answer("✅ Подвал новости сохранён!")

    # Кнопка по умолчанию
    @dp.callback_query(F.data.startswith("set_btn:"))
    async def edit_btn_start(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_btn_title)
        await callback.message.answer(
            "🔘 <b>Настройка постоянной инлайн-кнопки под постом</b>\n\n"
            "Отправьте текст кнопки (например: <code>👉 Наш чат</code>).\n"
            "Или отправьте <code>-</code>, чтобы убрать кнопку:",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_btn_title)
    async def edit_btn_title(message: types.Message, state: FSMContext):
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        if message.text.strip() == "-":
            await db.update_bot_setting(bot_id, "button_title", None)
            await db.update_bot_setting(bot_id, "button_url", None)
            await state.clear()
            await message.answer("✅ Кнопка удалена!")
            return

        await state.update_data(btn_title=message.text.strip())
        await state.set_state(SettingsStates.waiting_for_btn_url)
        await message.answer("🌐 Теперь отправьте ссылку для этой кнопки (https://...):")

    @dp.message(SettingsStates.waiting_for_btn_url)
    async def edit_btn_url(message: types.Message, state: FSMContext):
        url = message.text.strip()
        if not (url.startswith("http://") or url.startswith("https://") or url.startswith("tg://")):
            await message.answer("⚠️ Некорректная ссылка! Должна начинаться с https:// или tg://. Попробуйте снова:")
            return

        data = await state.get_data()
        bot_id = data["target_bot_id"]
        title = data["btn_title"]
        await db.update_bot_setting(bot_id, "button_title", title)
        await db.update_bot_setting(bot_id, "button_url", url)
        await state.clear()
        await message.answer(f"✅ Постоянная кнопка <b>[{title}]</b> сохранена!", parse_mode=ParseMode.HTML)

    # Кулдаун
    @dp.callback_query(F.data.startswith("set_cd:"))
    async def edit_cd_start(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_cooldown)
        await callback.message.answer("⏱ Введите задержку (антифлуд) между отправками новостей в секундах (например: <code>10</code>):", parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_cooldown)
    async def edit_cd_finish(message: types.Message, state: FSMContext):
        try:
            sec = int(message.text.strip())
            if sec < 0:
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Введите положительное целое число секунд.")
            return

        data = await state.get_data()
        bot_id = data["target_bot_id"]
        await db.update_bot_setting(bot_id, "cooldown_seconds", sec)
        await state.clear()
        await message.answer(f"✅ Антифлуд установлен на {sec} сек.")

    # ============== Управление тегами ==============
    @dp.callback_query(F.data.startswith("bot_tags:"))
    async def view_bot_tags(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        bot_id = int(callback.data.split(":")[1])
        tags = await db.get_bot_tags(bot_id)

        rows = []
        for t in tags:
            rows.append([
                InlineKeyboardButton(text=f"❌ {t['tag_name']}", callback_data=f"del_tag:{bot_id}:{t['id']}")
            ])

        rows.append([InlineKeyboardButton(text="➕ Добавить тег", callback_data=f"add_tag_prompt:{bot_id}")])
        rows.append([InlineKeyboardButton(text="◀️ Назад в меню бота", callback_data=f"bot_manage:{bot_id}")])

        text = (
            f"🏷 <b>Управление рубриками и тегами</b> (Бот #{bot_id})\n\n"
            f"Текущие теги (нажмите на тег, чтобы удалить его):\n"
        )
        if not tags:
            text += "<i>Теги пока не добавлены.</i>"
        else:
            text += "\n".join([f"• <code>{t['tag_name']}</code>" for t in tags])

        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data.startswith("del_tag:"))
    async def delete_tag_cb(callback: types.CallbackQuery, state: FSMContext):
        _, bot_id_str, tag_id_str = callback.data.split(":")
        bot_id = int(bot_id_str)
        tag_id = int(tag_id_str)
        await db.delete_bot_tag(bot_id, tag_id)
        await callback.answer("Тег удалён.")
        await view_bot_tags(callback, state)

    @dp.callback_query(F.data.startswith("add_tag_prompt:"))
    async def add_tag_prompt(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_new_tag)
        await callback.message.answer("✍️ Напишите новый тег (например: <code>#технологии</code> или <code>#спорт</code>):", parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_new_tag)
    async def add_tag_finish(message: types.Message, state: FSMContext):
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        tag_name = message.text.strip()
        ok = await db.add_bot_tag(bot_id, tag_name)
        await state.clear()
        if ok:
            await message.answer(f"✅ Тег <code>{tag_name}</code> успешно добавлен!", parse_mode=ParseMode.HTML)
        else:
            await message.answer(f"⚠️ Такой тег уже есть в списке.")

    # ============== Управление редакторами ==============
    @dp.callback_query(F.data.startswith("bot_admins:"))
    async def view_bot_admins(callback: types.CallbackQuery, state: FSMContext):
        await state.clear()
        bot_id = int(callback.data.split(":")[1])
        admins = await db.get_bot_admins(bot_id)

        rows = []
        for a in admins:
            role_badge = "👑 Владелец" if a["role"] == "owner" else "✏️ Редактор"
            btn_text = f"{role_badge}: ID {a['user_id']}"
            if a["role"] != "owner":
                btn_text += " (❌ удалить)"
                rows.append([InlineKeyboardButton(text=btn_text, callback_data=f"del_admin:{bot_id}:{a['user_id']}")])
            else:
                rows.append([InlineKeyboardButton(text=btn_text, callback_data="noop")])

        rows.append([InlineKeyboardButton(text="➕ Добавить редактора", callback_data=f"add_admin_prompt:{bot_id}")])
        rows.append([InlineKeyboardButton(text="◀️ Назад в меню бота", callback_data=f"bot_manage:{bot_id}")])

        text = (
            f"👥 <b>Редакторы и авторы бота #{bot_id}</b>\n\n"
            f"Редакторы могут присылать новости в бота и сразу публиковать их в канал.\n\n"
            f"Список пользователей с доступом:\n"
        )
        for a in admins:
            r_str = "👑 Владелец" if a["role"] == "owner" else "✏️ Редактор"
            text += f"• <code>{a['user_id']}</code> — {r_str}\n"

        await callback.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode=ParseMode.HTML)
        await callback.answer()

    @dp.callback_query(F.data.startswith("del_admin:"))
    async def delete_admin_cb(callback: types.CallbackQuery, state: FSMContext):
        _, bot_id_str, user_id_str = callback.data.split(":")
        bot_id = int(bot_id_str)
        uid = int(user_id_str)
        await db.remove_bot_admin(bot_id, uid)
        await callback.answer("Редактор удалён.")
        await view_bot_admins(callback, state)

    @dp.callback_query(F.data.startswith("add_admin_prompt:"))
    async def add_admin_prompt(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_new_admin_id)
        await callback.message.answer(
            "✍️ Отправьте <b>числовой Telegram ID</b> пользователя, которого хотите назначить редактором.\n\n"
            "Пользователь может узнать свой ID с помощью команды <code>/myid</code> или бота @userinfobot.",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_new_admin_id)
    async def add_admin_finish(message: types.Message, state: FSMContext):
        try:
            new_uid = int(message.text.strip())
        except ValueError:
            await message.answer("⚠️ ID должен состоять только из цифр. Попробуйте снова:")
            return

        data = await state.get_data()
        bot_id = data["target_bot_id"]
        ok = await db.add_bot_admin(bot_id, new_uid, role="editor")
        await state.clear()
        if ok:
            await message.answer(f"✅ Редактор <code>{new_uid}</code> успешно добавлен!", parse_mode=ParseMode.HTML)
        else:
            await message.answer("⚠️ Не удалось добавить редактора.")

    # ============== Смена канала ==============
    @dp.callback_query(F.data.startswith("bot_chnl:"))
    async def change_channel_prompt(callback: types.CallbackQuery, state: FSMContext):
        bot_id = int(callback.data.split(":")[1])
        await state.update_data(target_bot_id=bot_id)
        await state.set_state(SettingsStates.waiting_for_new_channel)
        await callback.message.answer(
            "📢 Отправьте новый <b>@username</b> или числовой <b>ID</b> канала для публикации:\n\n"
            "<i>(Не забудьте добавить бота администратором в этот канал!)</i>",
            parse_mode=ParseMode.HTML
        )
        await callback.answer()

    @dp.message(SettingsStates.waiting_for_new_channel)
    async def change_channel_finish(message: types.Message, state: FSMContext):
        new_channel = message.text.strip()
        data = await state.get_data()
        bot_id = data["target_bot_id"]
        bot_info = await db.get_bot_by_id(bot_id)

        title = new_channel
        try:
            temp_bot = Bot(token=bot_info["token"])
            chat = await temp_bot.get_chat(new_channel)
            title = chat.title or new_channel
            await temp_bot.session.close()
        except Exception:
            pass

        await db.update_bot_channel(bot_id, new_channel, title)
        await state.clear()
        await message.answer(f"✅ Канал изменён на <b>{title}</b> (<code>{new_channel}</code>)!", parse_mode=ParseMode.HTML)

    @dp.callback_query(F.data == "noop")
    async def noop_cb(callback: types.CallbackQuery):
        await callback.answer()

    return dp
