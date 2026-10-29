import aiosqlite
import time
import json
from typing import Optional, List, Dict, Any
from config import DB_PATH, DEFAULT_WATERMARK


async def init_db():
    """Инициализация таблиц базы данных."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                bot_username TEXT,
                bot_name TEXT,
                target_channel_id TEXT NOT NULL,
                channel_title TEXT,
                status TEXT DEFAULT 'active', -- 'active', 'stopped', 'error'
                mode TEXT DEFAULT 'direct',   -- 'direct' (только авторы/админы) или 'suggest' (предложка)
                created_at INTEGER
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                bot_id INTEGER PRIMARY KEY,
                watermark TEXT,
                header_format TEXT,
                footer_format TEXT,
                link_preview INTEGER DEFAULT 1,
                silent_mode INTEGER DEFAULT 0,
                protect_content INTEGER DEFAULT 0,
                button_title TEXT,
                button_url TEXT,
                allow_anon INTEGER DEFAULT 0,
                require_preview INTEGER DEFAULT 1,
                cooldown_seconds INTEGER DEFAULT 5,
                FOREIGN KEY (bot_id) REFERENCES bots (id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_tags (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                tag_name TEXT NOT NULL,
                is_default INTEGER DEFAULT 0,
                FOREIGN KEY (bot_id) REFERENCES bots (id) ON DELETE CASCADE
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT,
                role TEXT DEFAULT 'editor', -- 'owner', 'editor'
                FOREIGN KEY (bot_id) REFERENCES bots (id) ON DELETE CASCADE,
                UNIQUE (bot_id, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_bans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                reason TEXT,
                banned_at INTEGER,
                banned_by INTEGER,
                FOREIGN KEY (bot_id) REFERENCES bots (id) ON DELETE CASCADE,
                UNIQUE (bot_id, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_cooldowns (
                bot_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                last_time INTEGER,
                PRIMARY KEY (bot_id, user_id)
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bot_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                post_data TEXT NOT NULL, -- JSON-сериализованный пост
                scheduled_for INTEGER NOT NULL,
                status TEXT DEFAULT 'pending', -- 'pending', 'published', 'failed'
                created_at INTEGER,
                FOREIGN KEY (bot_id) REFERENCES bots (id) ON DELETE CASCADE
            )
        """)

        await db.commit()


# ================== БОТЫ (CRUD) ==================

async def create_bot(owner_id: int, token: str, bot_username: str, bot_name: str, 
                     target_channel_id: str, channel_title: str = "", mode: str = "direct") -> int:
    """Создаёт нового бота и базовые настройки."""
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("""
            INSERT INTO bots (owner_id, token, bot_username, bot_name, target_channel_id, channel_title, status, mode, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?)
        """, (owner_id, token, bot_username, bot_name, str(target_channel_id), channel_title, mode, now))
        bot_id = cursor.lastrowid

        # Создаём настройки по умолчанию
        await db.execute("""
            INSERT INTO bot_settings (bot_id, watermark, header_format, footer_format, link_preview, silent_mode, protect_content, require_preview)
            VALUES (?, ?, '', '', 1, 0, 0, 1)
        """, (bot_id, DEFAULT_WATERMARK))

        # Добавляем владельца в список админов бота
        await db.execute("""
            INSERT INTO bot_admins (bot_id, user_id, username, role)
            VALUES (?, ?, '', 'owner')
        """, (bot_id, owner_id))

        # Базовые теги
        default_tags = ["#новости", "#срочно", "#эксклюзив"]
        for tag in default_tags:
            await db.execute("INSERT INTO bot_tags (bot_id, tag_name, is_default) VALUES (?, ?, 0)", (bot_id, tag))

        await db.commit()
        return bot_id


async def get_bot_by_id(bot_id: int) -> Optional[Dict[str, Any]]:
    """Получает данные бота по ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE id = ?", (bot_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_bot_by_token(token: str) -> Optional[Dict[str, Any]]:
    """Получает данные бота по токену."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE token = ?", (token,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_bots() -> List[Dict[str, Any]]:
    """Возвращает всех ботов со статусом 'active'."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE status = 'active'") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_user_bots(owner_id: int) -> List[Dict[str, Any]]:
    """Получает список всех ботов, созданных пользователем."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bots WHERE owner_id = ? ORDER BY id DESC", (owner_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def update_bot_status(bot_id: int, status: str):
    """Обновляет статус бота ('active', 'stopped', 'error')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE bots SET status = ? WHERE id = ?", (status, bot_id))
        await db.commit()


async def update_bot_channel(bot_id: int, target_channel_id: str, channel_title: str = ""):
    """Обновляет целевой канал бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bots SET target_channel_id = ?, channel_title = ? WHERE id = ?",
            (str(target_channel_id), channel_title, bot_id)
        )
        await db.commit()


async def update_bot_mode(bot_id: int, mode: str):
    """Обновляет режим работы бота ('direct' или 'suggest')."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE bots SET mode = ? WHERE id = ?", (mode, bot_id))
        await db.commit()


async def delete_bot(bot_id: int):
    """Удаляет бота и все связанные данные."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bot_settings WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bot_tags WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bot_admins WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bot_bans WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bot_cooldowns WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM scheduled_posts WHERE bot_id = ?", (bot_id,))
        await db.execute("DELETE FROM bots WHERE id = ?", (bot_id,))
        await db.commit()


# ================== НАСТРОЙКИ (SETTINGS) ==================

async def get_bot_settings(bot_id: int) -> Dict[str, Any]:
    """Получает настройки форматирования и публикации для бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_settings WHERE bot_id = ?", (bot_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
            return {
                "bot_id": bot_id,
                "watermark": DEFAULT_WATERMARK,
                "header_format": "",
                "footer_format": "",
                "link_preview": 1,
                "silent_mode": 0,
                "protect_content": 0,
                "button_title": None,
                "button_url": None,
                "allow_anon": 0,
                "require_preview": 1,
                "cooldown_seconds": 5
            }


async def update_bot_setting(bot_id: int, key: str, value: Any):
    """Обновляет отдельную настройку бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(f"UPDATE bot_settings SET {key} = ? WHERE bot_id = ?", (value, bot_id))
        await db.commit()


# ================== ТЕГИ ==================

async def get_bot_tags(bot_id: int) -> List[Dict[str, Any]]:
    """Получает список тегов бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_tags WHERE bot_id = ? ORDER BY id ASC", (bot_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def add_bot_tag(bot_id: int, tag_name: str) -> bool:
    """Добавляет тег в список."""
    tag = tag_name.strip()
    if not tag.startswith("#"):
        tag = f"#{tag}"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bot_tags WHERE bot_id = ? AND tag_name = ?", (bot_id, tag)) as cur:
            if await cur.fetchone():
                return False
        await db.execute("INSERT INTO bot_tags (bot_id, tag_name) VALUES (?, ?)", (bot_id, tag))
        await db.commit()
        return True


async def delete_bot_tag(bot_id: int, tag_id: int):
    """Удаляет тег по ID."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bot_tags WHERE bot_id = ? AND id = ?", (bot_id, tag_id))
        await db.commit()


# ================== АДМИНИСТРАТОРЫ И РЕДАКТОРЫ ==================

async def is_bot_admin(bot_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь админом или редактором бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bot_admins WHERE bot_id = ? AND user_id = ?", (bot_id, user_id)) as cur:
            return await cur.fetchone() is not None


async def is_bot_owner(bot_id: int, user_id: int) -> bool:
    """Проверяет, является ли пользователь владельцем бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bots WHERE id = ? AND owner_id = ?", (bot_id, user_id)) as cur:
            return await cur.fetchone() is not None


async def get_bot_admins(bot_id: int) -> List[Dict[str, Any]]:
    """Возвращает список админов бота."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_admins WHERE bot_id = ?", (bot_id,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def add_bot_admin(bot_id: int, user_id: int, username: str = "", role: str = "editor") -> bool:
    """Добавляет редактора к боту."""
    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("""
                INSERT OR REPLACE INTO bot_admins (bot_id, user_id, username, role)
                VALUES (?, ?, ?, ?)
            """, (bot_id, user_id, username, role))
            await db.commit()
            return True
        except Exception:
            return False


async def remove_bot_admin(bot_id: int, user_id: int):
    """Удаляет редактора (владельца удалить нельзя)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM bot_admins WHERE bot_id = ? AND user_id = ? AND role != 'owner'", (bot_id, user_id))
        await db.commit()


# ================== БАНЫ И КУЛДАУН ==================

async def is_user_banned(bot_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT 1 FROM bot_bans WHERE bot_id = ? AND user_id = ?", (bot_id, user_id)) as cur:
            return await cur.fetchone() is not None


async def ban_user(bot_id: int, user_id: int, reason: str = "", banned_by: int = 0):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO bot_bans (bot_id, user_id, reason, banned_at, banned_by)
            VALUES (?, ?, ?, ?, ?)
        """, (bot_id, user_id, reason, int(time.time()), banned_by))
        await db.commit()


async def unban_user(bot_id: int, user_id: int) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("DELETE FROM bot_bans WHERE bot_id = ? AND user_id = ?", (bot_id, user_id))
        await db.commit()
        return cur.rowcount > 0


async def get_cooldown_remaining(bot_id: int, user_id: int, cooldown_seconds: int) -> int:
    """Возвращает оставшееся время кулдауна в секундах, 0 если можно публиковать."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT last_time FROM bot_cooldowns WHERE bot_id = ? AND user_id = ?", (bot_id, user_id)) as cur:
            row = await cur.fetchone()
            if not row:
                return 0
            diff = int(time.time()) - row[0]
            if diff < cooldown_seconds:
                return cooldown_seconds - diff
            return 0


async def update_cooldown(bot_id: int, user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO bot_cooldowns (bot_id, user_id, last_time)
            VALUES (?, ?, ?)
        """, (bot_id, user_id, int(time.time())))
        await db.commit()


# ================== ОТЛОЖЕННЫЕ ПОСТЫ ==================

async def add_scheduled_post(bot_id: int, user_id: int, post_data: dict, scheduled_for: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            INSERT INTO scheduled_posts (bot_id, user_id, post_data, scheduled_for, status, created_at)
            VALUES (?, ?, ?, ?, 'pending', ?)
        """, (bot_id, user_id, json.dumps(post_data), scheduled_for, int(time.time())))
        await db.commit()
        return cur.lastrowid


async def get_due_scheduled_posts() -> List[Dict[str, Any]]:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scheduled_posts WHERE status = 'pending' AND scheduled_for <= ?", (now,)) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def mark_scheduled_post(post_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE scheduled_posts SET status = ? WHERE id = ?", (status, post_id))
        await db.commit()


# ================== СТАТИСТИКА ==================

async def get_system_stats() -> Dict[str, Any]:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM bots") as cur:
            total_bots = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bots WHERE status = 'active'") as cur:
            active_bots = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM bot_admins") as cur:
            total_admins = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM scheduled_posts") as cur:
            total_scheduled = (await cur.fetchone())[0]

        return {
            "total_bots": total_bots,
            "active_bots": active_bots,
            "total_admins": total_admins,
            "total_scheduled": total_scheduled
        }
