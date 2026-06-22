"""多租户平台数据库 — P0.11 建表 + CRUD 辅助。

七表（对齐开发计划 Section 7.2，v2 审计修正）：
  users / bot_configs / channel_connections / chat_messages
  user_api_keys / user_blacklist / notifications
  + revoked_tokens（v2 H7：refresh token 黑名单，logout 立即吊销）

设计要点：
  - 复用 db_core.get_db()（全局单连接 + WAL + busy_timeout）
  - 独立于现有 memories/affection/bot_mood 单用户表，物理隔离
  - chat_messages 带 (bot_id, client_id) UNIQUE INDEX 支持 WS 幂等去重（S3）
  - data_permissions 6 开关（S7：存开关，执行点在 Pipeline 各 stage）
  - 与现有 web_admin ADMIN_API_KEY 认证物理隔离（S6）

注意：本模块不依赖 nonebot，可从 FastAPI 8766 进程独立调用。
"""
import json
import time
from typing import Any
from typing import Optional

from .config import DB_PATH  # 复用现有 DB 路径配置


# ============================================================
# 建表
# ============================================================

async def init_platform_tables() -> None:
    """初始化多租户七表 + revoked_tokens。

    幂等：CREATE TABLE IF NOT EXISTS。可重复调用。
    """
    from .db_core import get_db
    db = await get_db()

    # ── users 表（v2: data_permissions 6 开关，phone AES-256-GCM 双层）──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            phone_hash TEXT NOT NULL UNIQUE,
            phone_enc  TEXT NOT NULL,
            password   TEXT NOT NULL,
            nickname   TEXT DEFAULT '',
            avatar_url TEXT DEFAULT '',
            gender     TEXT DEFAULT '',
            custom_gender TEXT DEFAULT '',
            birthday   TEXT DEFAULT '',
            bio        TEXT DEFAULT '',
            data_permissions TEXT DEFAULT '{}',
            settings   TEXT DEFAULT '{}',
            is_admin   INTEGER DEFAULT 0,
            created_at REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        )
    """)

    # ── bot_configs 表（滑块 6 维 + persona_json）──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_configs (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL REFERENCES users(id),
            bot_name TEXT NOT NULL,
            personality TEXT NOT NULL DEFAULT 'gentle',
            avatar_url TEXT DEFAULT '',
            avatar_template TEXT DEFAULT '',
            persona_json TEXT DEFAULT '{}',
            abilities_json TEXT DEFAULT '{}',
            is_active INTEGER DEFAULT 1,
            created_at REAL DEFAULT 0,
            updated_at REAL DEFAULT 0
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_bot_configs_user ON bot_configs(user_id, is_active)"
    )

    # ── channel_connections 表 ──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS channel_connections (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id  INTEGER NOT NULL REFERENCES bot_configs(id),
            channel TEXT NOT NULL,
            status  TEXT DEFAULT 'disconnected',
            config_json TEXT DEFAULT '{}',
            connected_at REAL,
            created_at REAL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # ── chat_messages 表（S3: client_id 幂等去重 + bot_id/channel/sender_id/status/retry_count）──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            bot_id  INTEGER NOT NULL REFERENCES bot_configs(id),
            channel TEXT NOT NULL DEFAULT 'app',
            sender_id TEXT NOT NULL,
            content  TEXT NOT NULL,
            role  TEXT NOT NULL,
            client_id TEXT NOT NULL,
            message_type TEXT DEFAULT 'text',
            status TEXT DEFAULT 'pending',
            retry_count INTEGER DEFAULT 0,
            created_at REAL DEFAULT 0
        )
    """)
    await db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_client_id ON chat_messages(bot_id, client_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_bot_channel ON chat_messages(bot_id, channel)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_sender ON chat_messages(bot_id, sender_id)"
    )
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_messages_bot_created ON chat_messages(bot_id, created_at)"
    )

    # ── user_api_keys 表（v2: encrypted_key AES-256-GCM，前端只显示 key_suffix）──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_api_keys (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL REFERENCES users(id),
            provider TEXT NOT NULL,
            encrypted_key TEXT NOT NULL,
            key_suffix TEXT NOT NULL,
            name TEXT DEFAULT '',
            scopes TEXT DEFAULT '[]',
            is_active INTEGER DEFAULT 1,
            created_at REAL DEFAULT 0,
            last_used REAL DEFAULT 0
        )
    """)

    # ── user_blacklist 表 ──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_blacklist (
            id    INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id   INTEGER NOT NULL REFERENCES users(id),
            blocked_user_id INTEGER NOT NULL,
            blocked_name TEXT DEFAULT '',
            reason TEXT DEFAULT '',
            created_at REAL DEFAULT 0,
            UNIQUE(user_id, blocked_user_id)
        )
    """)

    # ── notifications 表 ──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id  INTEGER NOT NULL REFERENCES users(id),
            type  TEXT NOT NULL,
            title TEXT NOT NULL,
            body  TEXT DEFAULT '',
            is_read INTEGER DEFAULT 0,
            related_id TEXT DEFAULT NULL,
            created_at REAL DEFAULT 0
        )
    """)
    await db.execute(
        "CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read, created_at)"
    )

    # ── revoked_tokens 表（v2 H7：refresh token 黑名单）──
    await db.execute("""
        CREATE TABLE IF NOT EXISTS revoked_tokens (
            jti  TEXT PRIMARY KEY,
            user_id INTEGER,
            revoked_at REAL DEFAULT 0,
            expires_at REAL DEFAULT 0
        )
    """)

    await db.commit()


# ============================================================
# 默认值辅助
# ============================================================

DEFAULT_DATA_PERMISSIONS = {
    "ai_training": True,
    "learn_chat_style": True,
    "remember_interests": True,
    "usage_statistics": True,
    "crash_report": True,
    "third_party_sharing": False,
}

DEFAULT_SETTINGS = {
    "push_notification": True,
    "message_sound": True,
    "vibration": False,
    "ringtone": "default",
    "chat_bg_type": "default",
    "chat_bg_value": "",
    "theme": "light",
    "font_size": "medium",
}


# ============================================================
# users CRUD
# ============================================================

async def get_user_by_id(user_id: int) -> Optional[dict]:
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_user_by_phone_hash(phone_hash: str) -> Optional[dict]:
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT * FROM users WHERE phone_hash = ?", (phone_hash,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def create_user(
    phone_hash: str,
    phone_enc: str,
    password_hash: str,
    nickname: str = "",
) -> int:
    """创建用户，返回 user_id。"""
    from .db_core import get_db
    db = await get_db()
    now = time.time()
    async with db.execute(
        """INSERT INTO users
           (phone_hash, phone_enc, password, nickname, data_permissions, settings, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            phone_hash, phone_enc, password_hash, nickname,
            json.dumps(DEFAULT_DATA_PERMISSIONS, ensure_ascii=False),
            json.dumps(DEFAULT_SETTINGS, ensure_ascii=False),
            now, now,
        ),
    ) as cur:
        user_id = cur.lastrowid
    await db.commit()
    return user_id


async def update_user_profile(user_id: int, fields: dict) -> None:
    """PATCH profile，只更新 fields 中出现的列。"""
    from .db_core import get_db
    db = await get_db()
    allowed = ("nickname", "avatar_url", "gender", "custom_gender", "birthday", "bio")
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at = ?")
    vals.append(time.time())
    vals.append(user_id)
    await db.execute(f"UPDATE users SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()


async def update_user_settings(user_id: int, settings: dict) -> dict:
    """合并 settings（PATCH 语义），返回合并后完整 settings。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT settings FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    current = json.loads(row["settings"]) if row and row["settings"] else dict(DEFAULT_SETTINGS)
    current.update(settings)
    await db.execute(
        "UPDATE users SET settings = ?, updated_at = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False), time.time(), user_id),
    )
    await db.commit()
    return current


async def get_user_settings(user_id: int) -> dict:
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT settings FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return json.loads(row["settings"]) if row and row["settings"] else dict(DEFAULT_SETTINGS)


async def get_data_permissions(user_id: int) -> dict:
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT data_permissions FROM users WHERE id = ?", (user_id,)) as cur:
        row = await cur.fetchone()
    return json.loads(row["data_permissions"]) if row and row["data_permissions"] else dict(DEFAULT_DATA_PERMISSIONS)


async def set_data_permissions(user_id: int, perms: dict) -> dict:
    from .db_core import get_db
    db = await get_db()
    current = await get_data_permissions(user_id)
    current.update(perms)
    await db.execute(
        "UPDATE users SET data_permissions = ?, updated_at = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False), time.time(), user_id),
    )
    await db.commit()
    return current


# ============================================================
# bot_configs CRUD（含 H5 ownership 校验）
# ============================================================

async def create_bot(
    user_id: int,
    bot_name: str,
    personality: str = "gentle",
    persona_json: Optional[dict] = None,
    avatar_template: str = "",
) -> int:
    from .db_core import get_db
    db = await get_db()
    now = time.time()
    async with db.execute(
        """INSERT INTO bot_configs
           (user_id, bot_name, personality, avatar_template, persona_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            user_id, bot_name, personality, avatar_template,
            json.dumps(persona_json or {}, ensure_ascii=False),
            now, now,
        ),
    ) as cur:
        bot_id = cur.lastrowid
    await db.commit()
    return bot_id


async def get_bots_by_user(user_id: int) -> list[dict]:
    """列出用户的所有 Bot（H5: 自动按 user_id 过滤）。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT * FROM bot_configs WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_bot(bot_id: int) -> Optional[dict]:
    from .db_core import get_db
    db = await get_db()
    async with db.execute("SELECT * FROM bot_configs WHERE id = ?", (bot_id,)) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def get_bot_for_user(bot_id: int, user_id: int) -> Optional[dict]:
    """H5 ownership 校验：只返回属于 user_id 的 Bot，否则 None。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT * FROM bot_configs WHERE id = ? AND user_id = ?",
        (bot_id, user_id),
    ) as cur:
        row = await cur.fetchone()
    return dict(row) if row else None


async def update_bot(bot_id: int, fields: dict) -> None:
    from .db_core import get_db
    db = await get_db()
    allowed = ("bot_name", "personality", "avatar_url", "avatar_template", "persona_json", "is_active")
    sets = []
    vals = []
    for k, v in fields.items():
        if k in allowed:
            if k == "persona_json" and isinstance(v, dict):
                v = json.dumps(v, ensure_ascii=False)
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    sets.append("updated_at = ?")
    vals.append(time.time())
    vals.append(bot_id)
    await db.execute(f"UPDATE bot_configs SET {', '.join(sets)} WHERE id = ?", vals)
    await db.commit()


async def delete_bot(bot_id: int) -> None:
    """删除 Bot + 级联删除消息（chat_messages 无 FK 级联，手动删）。"""
    from .db_core import get_db
    db = await get_db()
    await db.execute("DELETE FROM chat_messages WHERE bot_id = ?", (bot_id,))
    await db.execute("DELETE FROM channel_connections WHERE bot_id = ?", (bot_id,))
    await db.execute("DELETE FROM bot_configs WHERE id = ?", (bot_id,))
    await db.commit()


# ============================================================
# chat_messages CRUD（S3: client_id 幂等）
# ============================================================

async def save_message(
    bot_id: int,
    sender_id: str,
    content: str,
    role: str,
    client_id: str,
    channel: str = "app",
    message_type: str = "text",
    status: str = "replied",
) -> tuple[int, bool]:
    """保存消息，返回 (message_id, created)。
    created=False 表示 client_id 重复（幂等命中），返回既有 id。
    """
    from .db_core import get_db
    db = await get_db()
    # 幂等查重
    async with db.execute(
        "SELECT id FROM chat_messages WHERE bot_id = ? AND client_id = ?",
        (bot_id, client_id),
    ) as cur:
        existing = await cur.fetchone()
    if existing:
        return existing["id"], False
    now = time.time()
    async with db.execute(
        """INSERT INTO chat_messages
           (bot_id, channel, sender_id, content, role, client_id, message_type, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (bot_id, channel, sender_id, content, role, client_id, message_type, status, now),
    ) as cur:
        msg_id = cur.lastrowid
    await db.commit()
    return msg_id, True


async def get_messages(
    bot_id: int,
    cursor: Optional[float] = None,
    limit: int = 50,
) -> list[dict]:
    """游标分页：cursor = 上一批最早消息的 created_at，取早于它的记录。"""
    from .db_core import get_db
    db = await get_db()
    if cursor is not None:
        async with db.execute(
            "SELECT * FROM chat_messages WHERE bot_id = ? AND created_at < ? ORDER BY created_at DESC LIMIT ?",
            (bot_id, cursor, limit),
        ) as cur:
            rows = await cur.fetchall()
    else:
        async with db.execute(
            "SELECT * FROM chat_messages WHERE bot_id = ? ORDER BY created_at DESC LIMIT ?",
            (bot_id, limit),
        ) as cur:
            rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ============================================================
# revoked_tokens（H7: refresh 黑名单）
# ============================================================

async def revoke_token(jti: str, user_id: int, expires_at: float) -> None:
    from .db_core import get_db
    db = await get_db()
    await db.execute(
        "INSERT OR REPLACE INTO revoked_tokens (jti, user_id, revoked_at, expires_at) VALUES (?, ?, ?, ?)",
        (jti, user_id, time.time(), expires_at),
    )
    await db.commit()


async def is_token_revoked(jti: str) -> bool:
    """H7: 查询 jti 是否在黑名单且未过期。

    - 不在表中 → False（从未吊销）
    - expires_at == 0 → True（永久吊销）
    - expires_at > now → True（仍在吊销窗口内）
    - expires_at <= now → False（吊销已过期，token 本身也已过期，无需再拦）
    """
    if not jti:
        return False
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT expires_at FROM revoked_tokens WHERE jti = ?",
        (jti,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return False
    exp = row["expires_at"]
    return exp == 0 or exp > time.time()


async def get_bot_abilities(bot_id: int) -> dict:
    """获取 Bot 能力配置。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT abilities_json FROM bot_configs WHERE id = ?", (bot_id,)
    ) as cur:
        row = await cur.fetchone()
    if not row:
        return {}
    return json.loads(row["abilities_json"]) if row["abilities_json"] else {}


async def update_bot_abilities(bot_id: int, abilities: dict) -> dict:
    """合并 Bot 能力配置（PATCH 语义），返回合并后完整 abilities。"""
    from .db_core import get_db
    db = await get_db()
    current = await get_bot_abilities(bot_id)
    current.update(abilities)
    await db.execute(
        "UPDATE bot_configs SET abilities_json = ?, updated_at = ? WHERE id = ?",
        (json.dumps(current, ensure_ascii=False), time.time(), bot_id),
    )
    await db.commit()
    return current


# ============================================================
# notifications CRUD（Task 1.10）
# ============================================================

_NOTIFICATION_TYPES = ("system", "msg", "bot", "update")


async def create_notification(
    user_id: int,
    type_: str,
    title: str,
    body: str = "",
    related_id: Optional[str] = None,
) -> int:
    """创建通知，返回 notification_id。"""
    if type_ not in _NOTIFICATION_TYPES:
        type_ = "system"
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        """INSERT INTO notifications (user_id, type, title, body, related_id, is_read, created_at)
           VALUES (?, ?, ?, ?, ?, 0, ?)""",
        (user_id, type_, title, body, related_id, time.time()),
    ) as cur:
        nid = cur.lastrowid
    await db.commit()
    return nid


async def get_notifications(
    user_id: int,
    type_: Optional[str] = None,
    unread: Optional[bool] = None,
    cursor: Optional[float] = None,
    limit: int = 50,
) -> list[dict]:
    """获取通知列表，游标分页 + 可选过滤。"""
    from .db_core import get_db
    db = await get_db()
    clauses = ["user_id = ?"]
    vals: list[Any] = [user_id]
    if type_:
        clauses.append("type = ?")
        vals.append(type_)
    if unread is True:
        clauses.append("is_read = 0")
    elif unread is False:
        clauses.append("is_read = 1")
    if cursor is not None:
        clauses.append("created_at < ?")
        vals.append(cursor)
    sql = f"SELECT * FROM notifications WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?"
    vals.append(limit)
    async with db.execute(sql, vals) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_unread_count(user_id: int) -> int:
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND is_read = 0",
        (user_id,),
    ) as cur:
        row = await cur.fetchone()
    return row["cnt"] if row else 0


async def mark_notification_read(user_id: int, notification_id: int) -> bool:
    """标记单条已读，返回是否找到并更新。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "UPDATE notifications SET is_read = 1 WHERE id = ? AND user_id = ?",
        (notification_id, user_id),
    ) as cur:
        affected = cur.rowcount
    await db.commit()
    return affected > 0


async def mark_all_notifications_read(user_id: int) -> int:
    """标记全部已读，返回更新条数。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
        (user_id,),
    ) as cur:
        affected = cur.rowcount
    await db.commit()
    return affected


# ============================================================
# user_blacklist CRUD（Task 1.2）
# ============================================================

async def get_blacklist(user_id: int) -> list[dict]:
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT * FROM user_blacklist WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def add_blacklist(user_id: int, blocked_user_id: int, blocked_name: str = "", reason: str = "") -> bool:
    """添加黑名单条目，返回是否新增（False=已在黑名单）。"""
    from .db_core import get_db
    db = await get_db()
    try:
        async with db.execute(
            "INSERT INTO user_blacklist (user_id, blocked_user_id, blocked_name, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, blocked_user_id, blocked_name, reason, time.time()),
        ) as cur:
            pass
        await db.commit()
        return True
    except Exception:
        return False


async def remove_blacklist(user_id: int, blocked_user_id: int) -> bool:
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "DELETE FROM user_blacklist WHERE user_id = ? AND blocked_user_id = ?",
        (user_id, blocked_user_id),
    ) as cur:
        affected = cur.rowcount
    await db.commit()
    return affected > 0


# ============================================================
# chat_messages search（Task 1.6 搜索）
# ============================================================

async def search_messages(
    user_id: int,
    q: str = "",
    bot_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """按关键词/日期范围搜索消息。只搜索属于 user_id 的 Bot 的消息。"""
    from .db_core import get_db
    db = await get_db()
    clauses = ["cm.role = 'user'"]  # 默认只搜用户消息
    vals: list[Any] = []
    # 权限：只搜当前用户的 Bot
    if bot_id is not None:
        clauses.append("cm.bot_id = ? AND bc.user_id = ?")
        vals.extend([bot_id, user_id])
    else:
        clauses.append("bc.user_id = ?")
        vals.append(user_id)
    if q:
        clauses.append("cm.content LIKE ?")
        vals.append(f"%{q}%")
    if date_from:
        from .utils import parse_chinese_date  # 装简单时间解析
        try:
            from_ts = parse_chinese_date(date_from)
            clauses.append("cm.created_at >= ?")
            vals.append(from_ts)
        except Exception:
            pass
    if date_to:
        try:
            to_ts = parse_chinese_date(date_to) + 86400
            clauses.append("cm.created_at <= ?")
            vals.append(to_ts)
        except Exception:
            pass
    sql = f"""SELECT cm.* FROM chat_messages cm
              JOIN bot_configs bc ON cm.bot_id = bc.id
              WHERE {' AND '.join(clauses)}
              ORDER BY cm.created_at DESC LIMIT ?"""
    vals.append(limit)
    async with db.execute(sql, vals) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


# ============================================================
# user_api_keys CRUD（Task 1.3 KMS）
# ============================================================

async def get_user_api_keys(user_id: int) -> list[dict]:
    """获取用户所有 API Key（前端只显示 key_suffix）。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT id, user_id, provider, key_suffix, name, scopes, is_active, created_at, last_used "
        "FROM user_api_keys WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def create_api_key(
    user_id: int,
    provider: str,
    encrypted_key: str,
    key_suffix: str,
    name: str = "",
    scopes: Optional[list] = None,
) -> int:
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        """INSERT INTO user_api_keys
           (user_id, provider, encrypted_key, key_suffix, name, scopes, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (user_id, provider, encrypted_key, key_suffix, name,
         json.dumps(scopes or [], ensure_ascii=False), time.time()),
    ) as cur:
        kid = cur.lastrowid
    await db.commit()
    return kid


async def revoke_api_key(user_id: int, key_id: int) -> bool:
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "UPDATE user_api_keys SET is_active = 0 WHERE id = ? AND user_id = ?",
        (key_id, user_id),
    ) as cur:
        affected = cur.rowcount
    await db.commit()
    return affected > 0


async def get_api_key_usage_summary(user_id: int) -> dict:
    """API Key 用量概览。"""
    from .db_core import get_db
    db = await get_db()
    keys = await get_user_api_keys(user_id)
    active = sum(1 for k in keys if k["is_active"])
    return {
        "total_keys": len(keys),
        "active_keys": active,
        "providers": list({k["provider"] for k in keys}),
    }


async def get_daily_quota(user_id: int) -> dict:
    """获取用户日限额使用情况。"""
    from .db_core import get_db
    db = await get_db()
    today_start = int(time.time() // 86400 * 86400)
    # 统计今天 chat_messages 中该用户 Bot 的 user 消息数
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM chat_messages cm
           JOIN bot_configs bc ON cm.bot_id = bc.id
           WHERE bc.user_id = ? AND cm.created_at >= ? AND cm.role = 'user'""",
        (user_id, today_start),
    ) as cur:
        row = await cur.fetchone()
    used = row["cnt"] if row else 0
    # 检查该用户是否有自带 Key
    keys = await get_user_api_keys(user_id)
    has_own_key = any(k["is_active"] for k in keys)
    return {
        "daily_used": used,
        "daily_limit": 999999 if has_own_key else 50,
        "tier": "user_key" if has_own_key else "platform",
    }


async def get_bot_graph_data(bot_id: int, days: int = 28) -> list[dict]:
    """获取 Bot 消息/情绪趋势（供仪表盘/数据面板使用）。"""
    from .db_core import get_db
    db = await get_db()
    since = time.time() - days * 86400
    async with db.execute(
        """SELECT strftime('%Y-%m-%d', created_at, 'unixepoch', 'localtime') as date,
                  COUNT(*) as cnt,
                  SUM(CASE WHEN role='user' THEN 1 ELSE 0 END) as user_msgs,
                  SUM(CASE WHEN role='bot' THEN 1 ELSE 0 END) as bot_msgs
           FROM chat_messages
           WHERE bot_id = ? AND created_at >= ?
           GROUP BY date ORDER BY date""",
        (bot_id, since),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def get_channel_status(bot_id: int) -> list[dict]:
    """获取 Bot 通道连接状态。"""
    from .db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT * FROM channel_connections WHERE bot_id = ?",
        (bot_id,),
    ) as cur:
        rows = await cur.fetchall()
    return [dict(r) for r in rows]


async def update_channel_status(
    bot_id: int,
    channel: str,
    status: str,
    config: Optional[dict] = None,
) -> None:
    """更新通道连接状态（UPSERT 语义）。"""
    from .db_core import get_db
    db = await get_db()
    now = time.time()
    await db.execute(
        """INSERT INTO channel_connections (bot_id, channel, status, config_json, connected_at, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(bot_id, channel) DO UPDATE SET
               status = excluded.status,
               config_json = excluded.config_json,
               connected_at = excluded.connected_at""",
        (bot_id, channel, status, json.dumps(config or {}, ensure_ascii=False), now, now),
    )
    await db.commit()
