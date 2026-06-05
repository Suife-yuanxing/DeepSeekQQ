"""数据库迁移机制 — ECC database-migrations 风格。

版本化迁移文件，按顺序执行，支持状态追踪。
"""
import aiosqlite
from typing import List, Callable, Coroutine, Any
from nonebot import logger


# 迁移列表：(版本号, 迁移函数)
MIGRATIONS: List[tuple] = []


def migration(version: int):
    """装饰器：注册一个数据库迁移。"""
    def decorator(func: Callable[[aiosqlite.Connection], Coroutine[Any, Any, None]]):
        MIGRATIONS.append((version, func))
        return func
    return decorator


async def ensure_migration_table(db: aiosqlite.Connection):
    """确保迁移状态表存在。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS _migrations (
            version INTEGER PRIMARY KEY,
            applied_at REAL,
            description TEXT
        )
    """)
    await db.commit()


async def get_current_version(db: aiosqlite.Connection) -> int:
    """获取当前数据库版本。"""
    await ensure_migration_table(db)
    async with db.execute("SELECT MAX(version) as v FROM _migrations") as cursor:
        row = await cursor.fetchone()
        if row and row[0] is not None:
            return row[0]
        return 0


async def run_migrations(db: aiosqlite.Connection):
    """执行所有未执行的迁移。"""
    current = await get_current_version(db)
    pending = [(v, f) for v, f in MIGRATIONS if v > current]
    if not pending:
        return

    for version, func in sorted(pending, key=lambda x: x[0]):
        try:
            logger.info(f"[迁移] 执行迁移 v{version}...")
            await func(db)
            import time
            await db.execute(
                "INSERT INTO _migrations (version, applied_at) VALUES (?, ?)",
                (version, time.time())
            )
            await db.commit()
            logger.info(f"[迁移] v{version} 完成")
        except Exception as e:
            logger.error(f"[迁移] v{version} 失败: {e}")
            raise


# ============================================================
# 迁移定义
# ============================================================

@migration(1)
async def migrate_v1_add_confidence(db: aiosqlite.Connection):
    """添加 confidence 和 hit_count 到 memory_tags。"""
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN confidence REAL DEFAULT 0.5")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN hit_count INTEGER DEFAULT 0")
    except Exception:
        pass
    await db.commit()


@migration(2)
async def migrate_v2_add_session_state(db: aiosqlite.Connection):
    """添加会话状态持久化表。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY,
            last_topic TEXT DEFAULT '',
            last_emotion TEXT DEFAULT '',
            last_interaction REAL DEFAULT 0,
            context_summary TEXT DEFAULT '',
            bot_mood_snapshot TEXT DEFAULT '{}'
        )
    """)
    await db.commit()


@migration(3)
async def migrate_v3_add_preferences_and_quality(db: aiosqlite.Connection):
    """添加用户偏好表和回复质量评估表（功能③⑦）。"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_preferences (
            user_id TEXT NOT NULL,
            pref_type TEXT NOT NULL,
            pref_key TEXT NOT NULL,
            pref_value REAL DEFAULT 0,
            sample_count INTEGER DEFAULT 0,
            last_updated REAL,
            UNIQUE(user_id, pref_type, pref_key)
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_user_pref ON user_preferences(user_id, pref_type)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reply_quality (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            reply_text TEXT NOT NULL,
            quality_score REAL DEFAULT 0,
            feedback_type TEXT,
            created_at REAL,
            emotion_at_reply TEXT,
            params_used TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_reply_quality_user ON reply_quality(user_id, created_at)")
    await db.commit()
