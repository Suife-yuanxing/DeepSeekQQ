"""数据库 Facade — 导入子模块并初始化所有表。

所有外部代码继续 `from .database import xxx`，无需修改。
"""
from datetime import datetime
from typing import Optional
from nonebot import logger

from .db_core import get_db, checkpoint_db, close_db
from .db_memories import (
    save_message, get_recent_memories, trim_memories, count_memories,
    get_oldest_memories, get_keep_ids, delete_memories_except,
    has_recent_message, has_user_message_today,
)
from .db_affection import (
    get_affection, update_affection, decay_affection,
    get_affection_decay_hint, check_and_trigger_milestone,
)
from .db_mood import (
    get_catgirl_mood, update_catgirl_mood,
    get_bot_mood, update_bot_mood,
    get_user_mood, update_user_mood, decay_user_mood,
)
from .db_tags import (
    save_memory_tags, decay_memory_tags, prune_memory_tags,
    get_relevant_memory_tags, boost_memory_tag,
)
from .db_session import (
    save_session_state, get_session_state, get_active_sessions,
    get_last_conversation_context, get_memory_summary, append_memory_summary,
    get_or_create_user_profile, update_user_profile,
    update_relationship_style, get_relationship_style,
    get_undisclosed_facts, mark_disclosed,
)
from .db_reminders import (
    save_reminder, get_due_reminders, mark_reminder_done,
    reschedule_reminder, get_user_reminders, cancel_reminder,
    find_reminder_by_content,
)
from .db_preferences import (
    get_user_preferences, get_top_preference, update_user_preference,
    save_reply_quality, get_quality_stats,
)
from .db_proactive import (
    get_today_proactive_count, log_proactive, get_recent_greetings,
    has_proactive_today, get_today_proactive_count_by_scene,
    get_silent_private_users,
)
from .db_cache import get_article_cache, save_article_cache

from .config import AFFECTION_LEVELS


# ============================================================
# 早晚安优化：昨晚聊天结束时间查询
# ============================================================

async def get_last_night_end_time(session_id: str) -> Optional[float]:
    """获取昨晚聊天结束时间（最后一条消息的时间戳）。

    用于动态调整早安时间：如果昨晚聊到很晚，第二天早安推迟。
    """
    from .db_core import get_db
    from datetime import datetime, timedelta
    db = await get_db()
    now = datetime.now()
    # 昨天 18:00 到今天 06:00 的消息
    yesterday_6pm = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0).timestamp()
    today_6am = now.replace(hour=6, minute=0, second=0).timestamp()
    async with db.execute(
        "SELECT MAX(timestamp) FROM memories WHERE session_id = ? AND timestamp BETWEEN ? AND ?",
        (session_id, yesterday_6pm, today_6am)
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            return float(row[0])
    return None


async def get_last_night_mood_summary(session_id: str) -> Optional[str]:
    """获取昨晚对话的情绪摘要（用于早安时携带上下文）。

    返回：'positive'/'negative'/'neutral'/None
    """
    from .db_core import get_db
    from datetime import datetime, timedelta
    db = await get_db()
    now = datetime.now()
    yesterday_6pm = (now - timedelta(days=1)).replace(hour=18, minute=0, second=0).timestamp()
    today_6am = now.replace(hour=6, minute=0, second=0).timestamp()

    # 查找昨晚最后几条 bot 回复
    async with db.execute(
        "SELECT content FROM memories WHERE session_id = ? AND role = 'assistant' AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC LIMIT 5",
        (session_id, yesterday_6pm, today_6am)
    ) as cursor:
        rows = await cursor.fetchall()
        if not rows:
            return None
        # 简单情绪判断
        last_msgs = " ".join(r[0] for r in rows)
        negative_kw = ["难过", "伤心", "不开心", "生气", "烦", "累", "焦虑", "担心", "害怕"]
        positive_kw = ["开心", "高兴", "哈哈", "笑", "棒", "好", "喜欢", "爱"]
        neg_count = sum(1 for kw in negative_kw if kw in last_msgs)
        pos_count = sum(1 for kw in positive_kw if kw in last_msgs)
        if neg_count > pos_count:
            return "negative"
        elif pos_count > neg_count:
            return "positive"
        return "neutral"


async def get_last_greeting_time(user_id: str, greeting_type: str) -> Optional[float]:
    """获取上一次发送早安/晚安的时间戳。

    用于控制问候频率，避免每天机械发送。
    """
    from .db_core import get_db
    db = await get_db()
    scene = "morning" if greeting_type == "morning" else "night"
    async with db.execute(
        "SELECT MAX(timestamp) FROM proactive_log WHERE user_id = ? AND scene IN (?, ?) AND type = 'private'",
        (user_id, scene, f"{scene}_triggered")
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            return float(row[0])
    return None


async def record_farewell(user_id: str, session_id: str):
    """记录用户道别时间（用于晚安后调侃逻辑）。"""
    from .db_core import get_db
    import time
    db = await get_db()
    # 使用 session_state 表记录道别时间
    await db.execute(
        "UPDATE session_state SET bot_mood_snapshot = ? WHERE session_id = ?",
        (f'{{"farewell_time": {time.time()}}}', session_id)
    )
    await db.commit()


async def get_last_farewell_time(session_id: str) -> Optional[float]:
    """获取上次道别时间。"""
    from .db_core import get_db
    import json
    db = await get_db()
    async with db.execute(
        "SELECT bot_mood_snapshot FROM session_state WHERE session_id = ?",
        (session_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if row and row[0]:
            try:
                data = json.loads(row[0])
                return data.get("farewell_time")
            except (json.JSONDecodeError, TypeError):
                pass
    return None


async def init_db():
    """初始化所有表和索引。"""
    db = await get_db()
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS affection (
            user_id TEXT PRIMARY KEY,
            score REAL DEFAULT 0,
            level INTEGER DEFAULT 1,
            title TEXT DEFAULT "陌生人",
            last_interaction REAL,
            total_chats INTEGER DEFAULT 0,
            streak_days INTEGER DEFAULT 0,
            last_streak_date TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS catgirl_mood (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            mood TEXT DEFAULT "平淡",
            score REAL DEFAULT 50,
            last_updated REAL
        )
    """)
    await db.execute(
        "INSERT OR IGNORE INTO catgirl_mood (id, mood, score, last_updated) VALUES (1, '平淡', 50, ?)",
        (datetime.now().timestamp(),)
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS bot_mood (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            valence REAL DEFAULT 0.0,
            arousal REAL DEFAULT 0.2,
            dominant TEXT DEFAULT '平静',
            trigger_reason TEXT DEFAULT '',
            trigger_time REAL DEFAULT 0,
            last_updated REAL DEFAULT 0
        )
    """)
    await db.execute(
        "INSERT OR IGNORE INTO bot_mood (id, valence, arousal, dominant, trigger_reason, trigger_time, last_updated) "
        "VALUES (1, 0.0, 0.2, '平静', '', 0, ?)",
        (datetime.now().timestamp(),)
    )
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            content TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            confidence REAL DEFAULT 0.5,
            hit_count INTEGER DEFAULT 0,
            created_at REAL,
            last_used REAL,
            UNIQUE(user_id, tag_type, content)
        )
    """)
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN confidence REAL DEFAULT 0.5")
    except Exception:
        pass
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN hit_count INTEGER DEFAULT 0")
    except Exception:
        pass
    await db.execute("""
        CREATE TABLE IF NOT EXISTS memory_summaries (
            session_id TEXT PRIMARY KEY,
            summary TEXT,
            key_moments TEXT,
            updated_at REAL
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_session ON memories(session_id, timestamp)")
    await db.execute("CREATE INDEX IF NOT EXISTS idx_memories_role ON memories(role, timestamp)")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS article_cache (
            url_hash TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            author TEXT,
            summary TEXT,
            fetched_at REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS proactive_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT,
            timestamp REAL NOT NULL,
            scene TEXT DEFAULT ''
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_mood (
            user_id TEXT PRIMARY KEY,
            valence REAL DEFAULT 0,
            arousal REAL DEFAULT 0.2,
            dominant TEXT DEFAULT '平静',
            last_updated REAL
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            content TEXT NOT NULL,
            trigger_time REAL NOT NULL,
            repeat_type TEXT DEFAULT 'none',
            status TEXT DEFAULT 'pending',
            created_at REAL NOT NULL,
            original_msg TEXT
        )
    """)
    await db.execute("CREATE INDEX IF NOT EXISTS idx_reminders_trigger ON reminders(status, trigger_time)")
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
