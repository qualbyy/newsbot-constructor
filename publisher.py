import logging
from typing import List, Optional, Dict, Any
from aiogram import Bot, types
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton,
    InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation
)
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

logger = logging.getLogger(__name__)


def build_final_text(
    body_html: str,
    tag: Optional[str] = None,
    watermark: str = "",
    header_format: str = "",
    footer_format: str = "",
) -> str:
    """
    Формирует итоговый HTML текст публикации с учётом тегов, водяного знака,
    шапки и подвала.
    """
    parts = []

    # Шапка
    if header_format:
        hdr = header_format.replace("{tag}", tag or "")
        parts.append(hdr.strip())

    # Тело новости (с сохранением HTML сущностей)
    if body_html:
        parts.append(body_html.strip())

    # Подвал / водяной знак
    footer_parts = []
    if footer_format:
        ftr = footer_format.replace("{tag}", tag or "")
        footer_parts.append(ftr.strip())

    if watermark:
        wm = watermark.replace("{tag}", tag or "")
        footer_parts.append(wm.strip())
    elif tag and not header_format and not footer_format:
        # Если нет шаблона, но выбран тег, добавляем его в конец
        footer_parts.append(tag.strip())

    if footer_parts:
        parts.append("\n\n" + "\n".join(footer_parts))

    return "\n\n".join(p for p in parts if p)


def build_post_keyboard(settings: Dict[str, Any], custom_btn_title: Optional[str] = None, custom_btn_url: Optional[str] = None) -> Optional[InlineKeyboardMarkup]:
    """Строит инлайн-кнопки под постом в канале."""
    buttons = []

    # Пользовательская кнопка для конкретного поста
    if custom_btn_title and custom_btn_url:
        buttons.append([InlineKeyboardButton(text=custom_btn_title, url=custom_btn_url)])

    # Стандартная кнопка из настроек бота
    btn_title = settings.get("button_title")
    btn_url = settings.get("button_url")
    if btn_title and btn_url and not (custom_btn_title == btn_title and custom_btn_url == btn_url):
        buttons.append([InlineKeyboardButton(text=btn_title, url=btn_url)])

    return InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None


async def send_post_to_target(
    bot: Bot,
    target_chat_id: str | int,
    post_data: Dict[str, Any],
    settings: Dict[str, Any],
    reply_markup: Optional[InlineKeyboardMarkup] = None,
) -> Any:
    """
    Отправляет подготовленный пост в канал или чат (или в ЛС для превью).
    Поддерживает одиночные медиа, альбомы, текст, сохраняя все HTML-теги.
    """
    tag = post_data.get("selected_tag")
    body_html = post_data.get("text_html") or ""
    watermark = settings.get("watermark") or ""
    header_fmt = settings.get("header_format") or ""
    footer_fmt = settings.get("footer_format") or ""

    final_text = build_final_text(
        body_html=body_html,
        tag=tag,
        watermark=watermark,
        header_format=header_fmt,
        footer_format=footer_fmt
    )

    # Лимиты телеграма
    max_len = 1024 if (post_data.get("media") or post_data.get("media_type")) else 4096
    if len(final_text) > max_len:
        final_text = final_text[:max_len - 10] + "..."

    protect_content = bool(settings.get("protect_content", 0))
    disable_notification = bool(settings.get("silent_mode", 0))
    link_preview = bool(settings.get("link_preview", 1))

    media_items = post_data.get("media")
    media_type = post_data.get("media_type")
    file_id = post_data.get("media_file_id")

    # 1. Альбом (Media Group)
    if media_items and len(media_items) > 1:
        group = []
        for i, m in enumerate(media_items):
            caption = final_text if i == 0 else None
            m_type = m.get("type")
            m_fid = m.get("file_id")

            if m_type == "photo":
                group.append(InputMediaPhoto(media=m_fid, caption=caption, parse_mode=ParseMode.HTML))
            elif m_type == "video":
                group.append(InputMediaVideo(media=m_fid, caption=caption, parse_mode=ParseMode.HTML))
            elif m_type == "document":
                group.append(InputMediaDocument(media=m_fid, caption=caption, parse_mode=ParseMode.HTML))
            elif m_type == "animation":
                group.append(InputMediaAnimation(media=m_fid, caption=caption, parse_mode=ParseMode.HTML))

        msgs = await bot.send_media_group(
            chat_id=target_chat_id,
            media=group,
            protect_content=protect_content,
            disable_notification=disable_notification
        )
        # Если к альбому нужны кнопки, они отправляются отдельным сообщением под альбомом
        if reply_markup and msgs:
            await bot.send_message(
                chat_id=target_chat_id,
                text="🔗 <i>Ссылки и действия:</i>",
                reply_markup=reply_markup,
                reply_to_message_id=msgs[0].message_id,
                parse_mode=ParseMode.HTML,
                disable_notification=disable_notification
            )
        return msgs[0] if msgs else None

    # 2. Одиночное фото
    if media_type == "photo" or (media_items and len(media_items) == 1 and media_items[0].get("type") == "photo"):
        fid = file_id or media_items[0]["file_id"]
        return await bot.send_photo(
            chat_id=target_chat_id,
            photo=fid,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification
        )

    # 3. Видео
    if media_type == "video" or (media_items and len(media_items) == 1 and media_items[0].get("type") == "video"):
        fid = file_id or media_items[0]["file_id"]
        return await bot.send_video(
            chat_id=target_chat_id,
            video=fid,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification
        )

    # 4. GIF / Анимация
    if media_type == "animation" or (media_items and len(media_items) == 1 and media_items[0].get("type") == "animation"):
        fid = file_id or media_items[0]["file_id"]
        return await bot.send_animation(
            chat_id=target_chat_id,
            animation=fid,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification
        )

    # 5. Документ
    if media_type == "document" or (media_items and len(media_items) == 1 and media_items[0].get("type") == "document"):
        fid = file_id or media_items[0]["file_id"]
        return await bot.send_document(
            chat_id=target_chat_id,
            document=fid,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification
        )

    # 6. Голосовое сообщение
    if media_type == "voice" or post_data.get("voice"):
        fid = file_id or post_data.get("voice")
        return await bot.send_voice(
            chat_id=target_chat_id,
            voice=fid,
            caption=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification
        )

    # 7. Текстовое сообщение
    if final_text:
        return await bot.send_message(
            chat_id=target_chat_id,
            text=final_text,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
            protect_content=protect_content,
            disable_notification=disable_notification,
            disable_web_page_preview=not link_preview
        )

    return None
