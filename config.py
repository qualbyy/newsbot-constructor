import os
from dotenv import load_dotenv

load_dotenv()

# Токен главного бота-конструктора
MASTER_BOT_TOKEN = os.getenv("MASTER_BOT_TOKEN", "")

# ID главного администратора хостинга (владельца машины)
SUPERADMIN_ID = int(os.getenv("SUPERADMIN_ID", "0"))

# Путь к базе данных
DB_PATH = os.getenv("DB_PATH", "news_constructor.db")

# Настройки по умолчанию для новых ботов
DEFAULT_WATERMARK = "\n\n📢 Подпишись: @channel"
DEFAULT_COOLDOWN = 5
MAX_POST_TEXT_LENGTH = 4000
