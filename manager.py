import asyncio
import logging
import json
from typing import Dict, Optional, Tuple
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError, TelegramBadRequest

import database as db
from client_bot import get_client_dispatcher
from publisher import send_post_to_target, build_post_keyboard

logger = logging.getLogger(__name__)


class BotManager:
    """Управляет динамическим жизненным циклом всех клиентских ботов."""

    def __init__(self):
        self._bots: Dict[int, Bot] = {}
        self._tasks: Dict[int, asyncio.Task] = {}
        self._stop_events: Dict[int, asyncio.Event] = {}
        self._client_dp: Optional[Dispatcher] = None
        self._scheduler_task: Optional[asyncio.Task] = None

    def get_dispatcher(self) -> Dispatcher:
        if self._client_dp is None:
            self._client_dp = get_client_dispatcher()
        return self._client_dp

    async def start_bot(self, bot_id: int) -> Tuple[bool, str]:
        """Запускает конкретного бота по ID."""
        if bot_id in self._tasks and not self._tasks[bot_id].done():
            return True, "Бот уже запущен."

        bot_info = await db.get_bot_by_id(bot_id)
        if not bot_info:
            return False, "Бот не найден в базе данных."

        token = bot_info["token"]
        try:
            bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
            me = await bot.get_me()
        except TelegramUnauthorizedError:
            await db.update_bot_status(bot_id, "error")
            return False, "Недействительный токен Telegram бота!"
        except Exception as e:
            return False, f"Ошибка при проверке токена: {e}"

        stop_event = asyncio.Event()
        self._bots[bot_id] = bot
        self._stop_events[bot_id] = stop_event

        dp = self.get_dispatcher()
        task = asyncio.create_task(self._run_bot_worker(bot_id, bot, dp, stop_event))
        self._tasks[bot_id] = task

        await db.update_bot_status(bot_id, "active")
        logger.info(f"🟢 Бот @{me.username} (ID: {bot_id}) успешно запущен.")
        return True, f"Бот @{me.username} успешно запущен!"

    async def stop_bot(self, bot_id: int) -> bool:
        """Останавливает конкретного бота."""
        stop_event = self._stop_events.pop(bot_id, None)
        if stop_event:
            stop_event.set()

        task = self._tasks.pop(bot_id, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        bot = self._bots.pop(bot_id, None)
        if bot:
            try:
                await bot.session.close()
            except Exception:
                pass

        await db.update_bot_status(bot_id, "stopped")
        logger.info(f"🔴 Бот ID: {bot_id} остановлен.")
        return True

    async def restart_bot(self, bot_id: int) -> Tuple[bool, str]:
        """Перезапускает бота."""
        await self.stop_bot(bot_id)
        await asyncio.sleep(1)
        return await self.start_bot(bot_id)

    async def _run_bot_worker(self, bot_id: int, bot: Bot, dp: Dispatcher, stop_event: asyncio.Event):
        """Индивидуальный long-polling цикл для бота."""
        offset = 0
        try:
            await bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            logger.warning(f"Предупреждение при очистке вебхука для бота {bot_id}: {e}")

        while not stop_event.is_set():
            try:
                updates = await bot.get_updates(offset=offset, timeout=20, allowed_updates=["message", "callback_query"])
                for update in updates:
                    offset = update.update_id + 1
                    # Передаем bot_id в контекст хэндлеров aiogram
                    asyncio.create_task(dp.feed_update(bot, update, bot_id=bot_id))
            except asyncio.CancelledError:
                break
            except TelegramUnauthorizedError:
                logger.error(f"Бот {bot_id} потерял авторизацию (токен отозван).")
                await db.update_bot_status(bot_id, "error")
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле поллинга бота {bot_id}: {e}")
                await asyncio.sleep(3)

    async def start_all_active(self):
        """Запускает всех ботов со статусом 'active' из базы."""
        active_bots = await db.get_active_bots()
        logger.info(f"Запуск {len(active_bots)} активных ботов из БД...")
        for b in active_bots:
            try:
                ok, msg = await self.start_bot(b["id"])
                if not ok:
                    logger.warning(f"Не удалось запустить бота #{b['id']}: {msg}")
            except Exception as e:
                logger.error(f"Исключение при старте бота #{b['id']}: {e}")

        # Запускаем фоновый планировщик отложенных постов
        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def stop_all(self):
        """Останавливает всех ботов."""
        if self._scheduler_task:
            self._scheduler_task.cancel()

        bot_ids = list(self._tasks.keys())
        for bid in bot_ids:
            await self.stop_bot(bid)

    async def _scheduler_loop(self):
        """Фоновый таймер для публикации отложенных постов."""
        while True:
            try:
                due_posts = await db.get_due_scheduled_posts()
                for p in due_posts:
                    bot_id = p["bot_id"]
                    post_id = p["id"]
                    post_data = json.loads(p["post_data"])

                    bot = self._bots.get(bot_id)
                    if not bot:
                        logger.warning(f"Отложенный пост {post_id}: бот {bot_id} не активен.")
                        continue

                    bot_info = await db.get_bot_by_id(bot_id)
                    settings = await db.get_bot_settings(bot_id)
                    channel_id = bot_info["target_channel_id"]

                    try:
                        post_kb = build_post_keyboard(settings, post_data.get("custom_btn_title"), post_data.get("custom_btn_url"))
                        await send_post_to_target(bot, channel_id, post_data, settings, reply_markup=post_kb)
                        await db.mark_scheduled_post(post_id, "published")
                        logger.info(f"✅ Отложенный пост {post_id} успешно опубликован в {channel_id} через бота #{bot_id}")

                        # Уведомляем автора
                        try:
                            await bot.send_message(p["user_id"], f"🎉 <b>Ваш отложенный пост был опубликован в канале {channel_id}!</b>", parse_mode=ParseMode.HTML)
                        except Exception:
                            pass
                    except Exception as e:
                        logger.error(f"Ошибка публикации отложенного поста {post_id}: {e}")
                        await db.mark_scheduled_post(post_id, "failed")

                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в цикле планировщика: {e}")
                await asyncio.sleep(15)

    def is_bot_running(self, bot_id: int) -> bool:
        return bot_id in self._tasks and not self._tasks[bot_id].done()


# Синглтон менеджера
manager = BotManager()
