import sys
import os

# Автоматическое переключение на виртуальное окружение, если запущено через системный python
local_venv = os.path.join(os.path.dirname(os.path.abspath(__file__)), "venv", "bin", "python")
fallback_venv = os.path.expanduser("/home/qualbyy/TeakBot/venv/bin/python")
target_python = local_venv if os.path.exists(local_venv) else (fallback_venv if os.path.exists(fallback_venv) else None)

if target_python and sys.executable != target_python:
    try:
        import aiogram
    except ImportError:
        os.execv(target_python, [target_python] + sys.argv)

import asyncio
import logging
import signal
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import MASTER_BOT_TOKEN
import database as db
from manager import manager
from master_bot import get_master_dispatcher

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("constructor.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("NewsBotHost")


async def main():
    logger.info("🚀 Запуск сервера-хостинга новостных ботов...")

    # 1. Инициализация базы данных
    await db.init_db()
    logger.info("База данных SQLite инициализирована.")

    # 2. Проверка токена мастер-бота
    if not MASTER_BOT_TOKEN:
        logger.error(
            "❌ Переменная MASTER_BOT_TOKEN не задана!\n"
            "Пожалуйста, укажите токен главного бота в файле .env или переменных окружения.\n"
            "Пример в файле .env:\nMASTER_BOT_TOKEN=123456789:ABCDEF..."
        )
        sys.exit(1)

    master_bot = Bot(token=MASTER_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # Повторные попытки подключения к Telegram API при старте (защита от кратковременных сетевых сбоев)
    master_me = None
    for attempt in range(1, 6):
        try:
            master_me = await master_bot.get_me()
            logger.info(f"👑 Главный бот-конструктор: @{master_me.username} ({master_me.first_name})")
            break
        except Exception as e:
            logger.warning(f"Попытка {attempt}/5 подключения к Telegram API: {e}")
            if attempt == 5:
                logger.error("Не удалось подключиться к Telegram API после 5 попыток.")
                await master_bot.session.close()
                sys.exit(1)
            await asyncio.sleep(2)

    # 3. Запуск всех сохранённых активных клиентских ботов
    await manager.start_all_active()

    # 4. Запуск диспетчера мастер-бота
    master_dp = get_master_dispatcher()

    # Сбрасываем старые вебхуки
    await master_bot.delete_webhook(drop_pending_updates=True)

    # Graceful shutdown событие
    stop_event = asyncio.Event()

    def signal_handler():
        logger.info("Получен сигнал завершения. Остановка всех ботов...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            pass

    master_task = asyncio.create_task(master_dp.start_polling(master_bot))

    # Ждём завершения или сигнала остановки
    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Остановка процессов...")
        await master_dp.stop_polling()
        master_task.cancel()
        try:
            await master_task
        except asyncio.CancelledError:
            pass
        await manager.stop_all()
        await master_bot.session.close()
        logger.info("Все боты и соединения закрыты. До свидания!")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
