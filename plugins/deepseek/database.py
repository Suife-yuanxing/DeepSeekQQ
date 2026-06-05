"""数据库连接池 + 所有表操作。全局单连接复用，aiosqlite 线程安全无需 asyncio.Lock。"""
import asyncio
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from .config import DB_PATH, AFFECTION_LEVELS
from nonebot import logger

_db: Optional[aiosqlite.Connection] = None


async def get_db() -> aiosqlite.Connection:
    """获取全局数据库连接（延迟初始化，自动复用）。"""
    global _db
    if _db is None:
        _db = await aiosqlite.connect(DB_PATH)
        _db.row_factory = aiosqlite.Row
        await _db.execute("PRAGMA journal_mode=WAL")
        await _db.execute("PRAGMA synchronous=NORMAL")
    return _db


async def checkpoint_db():
    """手动触发 WAL checkpoint，防止日志无限膨胀。"""
    global _db
    if _db:
        try:
            await _db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            logger.error(f"[数据库] checkpoint 失败: {e}")


async def close_db():
    """关闭全局数据库连接。先 checkpoint 再关闭，确保 WAL 落盘。"""
    global _db
    if _db:
        await checkpoint_db()
        await _db.close()
        _db = None


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
    # 升级旧表：添加 confidence 和 hit_count 列（如果不存在）
    try:
        await db.execute("ALTER TABLE memory_tags ADD COLUMN confidence REAL DEFAULT 0.5")
    except Exception:
        pass  # 列已存在
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
            timestamp REAL NOT NULL
        )
    """)
    # Phase 2: 每用户独立情绪（VA模型）
    await db.execute("""
        CREATE TABLE IF NOT EXISTS user_mood (
            user_id TEXT PRIMARY KEY,
            valence REAL DEFAULT 0,
            arousal REAL DEFAULT 0.2,
            dominant TEXT DEFAULT '平静',
            last_updated REAL
        )
    """)
    # Phase 4: 备忘录/提醒
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
    # Phase: 用户偏好自学习（功能③）
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
    # Phase: 回复质量评估（功能⑦）
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


# ---------- memories ----------
async def save_message(session_id: str, role: str, content: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO memories (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
        (session_id, role, content, datetime.now().timestamp())
    )
    await db.commit()


async def get_recent_memories(session_id: str, limit: int = 30) -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT role, content, timestamp FROM memories WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
        (session_id, limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [{"role": r["role"], "content": r["content"], "timestamp": r["timestamp"]} for r in reversed(rows)]


async def trim_memories(session_id: str, keep: int = 30):
    db = await get_db()
    await db.execute(
        """DELETE FROM memories WHERE session_id = ?
           AND id NOT IN (
               SELECT id FROM memories WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?
           )""",
        (session_id, session_id, keep)
    )
    await db.commit()


async def count_memories(session_id: str) -> int:
    db = await get_db()
    async with db.execute("SELECT COUNT(*) as cnt FROM memories WHERE session_id = ?", (session_id,)) as cursor:
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


async def get_oldest_memories(session_id: str, limit: int = 15) -> List[aiosqlite.Row]:
    db = await get_db()
    async with db.execute(
        "SELECT role, content FROM memories WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
        (session_id, limit)
    ) as cursor:
        return await cursor.fetchall()


async def get_keep_ids(session_id: str, keep: int = 20) -> List[int]:
    db = await get_db()
    async with db.execute(
        "SELECT id FROM memories WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
        (session_id, keep)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["id"] for r in rows]


async def delete_memories_except(session_id: str, keep_ids: List[int]):
    if not keep_ids:
        return
    db = await get_db()
    placeholders = ",".join(["?"] * len(keep_ids))
    await db.execute(
        f"DELETE FROM memories WHERE session_id = ? AND id NOT IN ({placeholders})",
        (session_id, *keep_ids)
    )
    await db.commit()


# ---------- affection ----------
async def get_affection(user_id: str) -> Dict[str, Any]:
    db = await get_db()
    async with db.execute(
        "SELECT score, level, title, total_chats, streak_days FROM affection WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return {"score": 0, "level": 1, "title": "陌生人", "total_chats": 0, "streak_days": 0}
        return {
            "score": row["score"],
            "level": row["level"],
            "title": row["title"],
            "total_chats": row["total_chats"],
            "streak_days": row["streak_days"],
        }


async def update_affection(user_id: str, delta: float = 1.0):
    db = await get_db()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    async with db.execute(
        "SELECT score, total_chats, streak_days, last_streak_date FROM affection WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        await db.execute(
            """INSERT INTO affection
            (user_id, score, level, title, last_interaction, total_chats, streak_days, last_streak_date)
            VALUES (?, ?, 1, ?, ?, 1, 1, ?)""",
            (str(user_id), delta, AFFECTION_LEVELS[0][1], now.timestamp(), today)
        )
    else:
        score, total_chats, streak_days, last_streak = row
        new_score = max(0, score + delta)
        new_total = total_chats + 1
        if last_streak == today:
            new_streak = streak_days
        elif last_streak == (now - timedelta(days=1)).strftime("%Y-%m-%d"):
            new_streak = streak_days + 1
        else:
            new_streak = 1
        new_level = 1
        new_title = AFFECTION_LEVELS[0][1]
        for threshold, title in AFFECTION_LEVELS:
            if new_score >= threshold:
                new_level = AFFECTION_LEVELS.index((threshold, title)) + 1
                new_title = title
        await db.execute(
            """UPDATE affection
            SET score = ?, level = ?, title = ?, last_interaction = ?,
                total_chats = ?, streak_days = ?, last_streak_date = ?
            WHERE user_id = ?""",
            (new_score, new_level, new_title, now.timestamp(), new_total, new_streak, today, str(user_id))
        )
    await db.commit()


# ---------- mood ----------
async def get_catgirl_mood() -> Dict[str, Any]:
    db = await get_db()
    async with db.execute("SELECT mood, score FROM catgirl_mood WHERE id = 1") as cursor:
        row = await cursor.fetchone()
        return {"mood": row["mood"], "score": row["score"]}


async def update_catgirl_mood(user_msg: str) -> Dict[str, Any]:
    import random
    happy = ["开心", "喜欢", "爱", "棒", "可爱", "喵", "亲", "抱", "摸摸", "乖", "嘿嘿", "哈哈", "想", "好", "乖"]
    sad = ["累", "难过", "伤心", "哭", "烦", "滚", "讨厌", "傻", "笨", "坏", "丑"]
    delta = 5 if any(w in user_msg for w in happy) else -3 if any(w in user_msg for w in sad) else 0
    db = await get_db()
    async with db.execute("SELECT score FROM catgirl_mood WHERE id = 1") as cursor:
        row = await cursor.fetchone()
    new_score = max(0, min(100, row["score"] + delta + random.randint(-2, 2)))
    mood = "开心" if new_score > 70 else "平淡" if new_score > 40 else "傲娇" if new_score > 20 else "生气"
    await db.execute(
        "UPDATE catgirl_mood SET mood = ?, score = ?, last_updated = ? WHERE id = 1",
        (mood, new_score, datetime.now().timestamp())
    )
    await db.commit()
    return {"mood": mood, "score": new_score}


# ---------- bot mood (bot自己的情绪状态机) ----------
async def get_bot_mood() -> Dict[str, Any]:
    """获取bot自己的情绪状态。"""
    db = await get_db()
    async with db.execute(
        "SELECT valence, arousal, dominant, trigger_reason, trigger_time, last_updated FROM bot_mood WHERE id = 1"
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return {"valence": 0.0, "arousal": 0.2, "dominant": "平静", "trigger_reason": "", "trigger_time": 0, "last_updated": 0}
        return {
            "valence": row["valence"],
            "arousal": row["arousal"],
            "dominant": row["dominant"],
            "trigger_reason": row["trigger_reason"],
            "trigger_time": row["trigger_time"],
            "last_updated": row["last_updated"],
        }


async def update_bot_mood(valence: float, arousal: float, dominant: str, reason: str = ""):
    """更新bot的情绪状态。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        "UPDATE bot_mood SET valence=?, arousal=?, dominant=?, trigger_reason=?, trigger_time=?, last_updated=? WHERE id=1",
        (valence, arousal, dominant, reason, now, now)
    )
    await db.commit()


# ---------- memory summaries ----------
async def get_memory_summary(session_id: str) -> Optional[str]:
    db = await get_db()
    async with db.execute("SELECT summary FROM memory_summaries WHERE session_id = ?", (session_id,)) as cursor:
        row = await cursor.fetchone()
        return row["summary"] if row else None


async def append_memory_summary(session_id: str, summary: str):
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO memory_summaries (session_id, summary, key_moments, updated_at)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
           summary = summary || ' | ' || ?, updated_at = ?""",
        (session_id, summary, "[]", now, summary, now)
    )
    await db.commit()


# ---------- memory tags ----------
async def save_memory_tags(user_id: str, tags: List[Dict[str, str]]):
    """保存记忆标签，使用置信度评分系统。

    新标签初始置信度 0.5，重复提取时 +0.1（上限 0.95）。
    """
    db = await get_db()
    now = datetime.now().timestamp()
    for tag in tags:
        t_type = tag.get("type", "fact")
        content_text = tag.get("content", "").strip()
        if not content_text or len(content_text) > 200:
            continue
        await db.execute(
            """INSERT INTO memory_tags (user_id, tag_type, content, weight, confidence, hit_count, created_at, last_used)
               VALUES (?, ?, ?, 1.0, 0.5, 0, ?, ?)
               ON CONFLICT(user_id, tag_type, content)
               DO UPDATE SET weight = weight + 0.2,
                             confidence = MIN(0.95, confidence + 0.1),
                             hit_count = hit_count + 1,
                             last_used = ?""",
            (str(user_id), t_type, content_text, now, now, now)
        )
    await db.commit()


async def decay_memory_tags(user_id: str = None, decay_rate: float = 0.02):
    """对记忆标签做时间衰减。未使用的标签置信度逐渐降低。

    调度方式：每天运行一次。
    - decay_rate: 每次衰减的置信度减少量（默认 0.02，即 50 天未使用降到 0）
    """
    db = await get_db()
    now = datetime.now().timestamp()
    if user_id:
        await db.execute(
            """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
               WHERE user_id = ? AND last_used < ? - 86400""",
            (decay_rate, str(user_id), now)
        )
    else:
        await db.execute(
            """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
               WHERE last_used < ? - 86400""",
            (decay_rate, now)
        )
    await db.commit()


async def prune_memory_tags(min_confidence: float = 0.15):
    """清理置信度过低的记忆标签。

    调度方式：每天运行一次（在 decay_memory_tags 之后）。
    """
    db = await get_db()
    cursor = await db.execute(
        "DELETE FROM memory_tags WHERE confidence < ?", (min_confidence,)
    )
    await db.commit()
    deleted = cursor.rowcount
    if deleted > 0:
        logger.info(f"[记忆] 清理了 {deleted} 条低置信度标签")
    return deleted


async def get_relevant_memory_tags(user_id: str, limit: int = 5) -> List[aiosqlite.Row]:
    """获取相关记忆标签，按 置信度×权重 综合排序。"""
    db = await get_db()
    async with db.execute(
        """SELECT tag_type, content, weight, confidence, hit_count, last_used
           FROM memory_tags
           WHERE user_id = ? AND confidence >= 0.15
             AND (tag_type IN ('preference', 'fact', 'taboo') OR weight > 1.2)
           ORDER BY (confidence * weight) DESC, last_used DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        return await cursor.fetchall()


async def boost_memory_tag(user_id: str, content: str, boost: float = 0.1):
    """当记忆被成功引用时，提升其置信度和权重。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """UPDATE memory_tags SET confidence = MIN(0.95, confidence + ?),
               weight = weight + 0.05, last_used = ?
           WHERE user_id = ? AND content = ?""",
        (boost, now, str(user_id), content)
    )
    await db.commit()


# ---------- article cache ----------
async def get_article_cache(url_hash: str) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT title, author, summary, fetched_at FROM article_cache WHERE url_hash = ?",
        (url_hash,)
    ) as cursor:
        row = await cursor.fetchone()
        if row and datetime.now().timestamp() - row["fetched_at"] < 86400:
            return {"title": row["title"] or "无标题", "author": row["author"] or "未知", "summary": row["summary"] or "", "cached": True}
        return None


async def save_article_cache(url_hash: str, url: str, title: str, author: str, summary: str):
    db = await get_db()
    await db.execute(
        """INSERT OR REPLACE INTO article_cache
        (url_hash, url, title, author, summary, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (url_hash, url, title, author, summary[:2000], datetime.now().timestamp())
    )
    await db.commit()


# ---------- proactive log ----------
async def get_today_proactive_count(user_id: str, today: str) -> int:
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND type = 'private'
           AND datetime(timestamp, 'unixepoch', 'localtime') LIKE ?""",
        (user_id, f"{today}%")
    ) as cursor:
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


async def log_proactive(user_id: str, msg_type: str, content: str):
    db = await get_db()
    await db.execute(
        "INSERT INTO proactive_log (user_id, type, content, timestamp) VALUES (?, ?, ?, ?)",
        (user_id, msg_type, content[:200], datetime.now().timestamp())
    )
    await db.commit()


# ---------- silence check ----------
async def get_silent_private_users(threshold: float) -> List[str]:
    db = await get_db()
    async with db.execute(
        """SELECT session_id, MAX(timestamp) as last_time
           FROM memories WHERE session_id LIKE 'private_%'
           GROUP BY session_id HAVING last_time < ?""",
        (threshold,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["session_id"].replace("private_", "") for r in rows]


# ---------- user_mood (Phase 2: VA情绪模型) ----------
async def get_user_mood(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户专属情绪状态。"""
    db = await get_db()
    async with db.execute(
        "SELECT valence, arousal, dominant, last_updated FROM user_mood WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "valence": row["valence"],
            "arousal": row["arousal"],
            "dominant": row["dominant"],
            "last_updated": row["last_updated"],
        }


async def update_user_mood(user_id: str, valence: float, arousal: float, dominant: str):
    """更新用户情绪状态。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO user_mood (user_id, valence, arousal, dominant, last_updated)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id) DO UPDATE SET
           valence = ?, arousal = ?, dominant = ?, last_updated = ?""",
        (str(user_id), valence, arousal, dominant, now,
         valence, arousal, dominant, now)
    )
    await db.commit()


async def decay_user_mood(user_id: str, decay_factor: float = 0.9):
    """对用户情绪做自然衰减（向平静回归）。"""
    db = await get_db()
    async with db.execute(
        "SELECT valence, arousal FROM user_mood WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return
    new_v = row["valence"] * decay_factor
    new_a = row["arousal"] * decay_factor
    now = datetime.now().timestamp()
    await db.execute(
        "UPDATE user_mood SET valence = ?, arousal = ?, last_updated = ? WHERE user_id = ?",
        (new_v, new_a, now, str(user_id))
    )
    await db.commit()


# ---------- reminders (Phase 4: 备忘录) ----------
async def save_reminder(user_id: str, session_id: str, content: str,
                        trigger_time: float, repeat_type: str = "none",
                        original_msg: str = "") -> int:
    """创建提醒，返回 reminder id。"""
    db = await get_db()
    now = datetime.now().timestamp()
    cursor = await db.execute(
        """INSERT INTO reminders (user_id, session_id, content, trigger_time, repeat_type, status, created_at, original_msg)
           VALUES (?, ?, ?, ?, ?, 'pending', ?, ?)""",
        (str(user_id), session_id, content, trigger_time, repeat_type, now, original_msg)
    )
    await db.commit()
    return cursor.lastrowid


async def get_due_reminders() -> List[Dict[str, Any]]:
    """获取所有到期的提醒（status=pending 且 trigger_time <= now）。"""
    db = await get_db()
    now = datetime.now().timestamp()
    async with db.execute(
        """SELECT id, user_id, session_id, content, trigger_time, repeat_type, original_msg
           FROM reminders WHERE status = 'pending' AND trigger_time <= ?
           ORDER BY trigger_time ASC""",
        (now,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def mark_reminder_done(reminder_id: int):
    """标记提醒为已完成。"""
    db = await get_db()
    await db.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (reminder_id,))
    await db.commit()


async def reschedule_reminder(reminder_id: int, next_trigger: float):
    """重复提醒：更新下一次触发时间。"""
    db = await get_db()
    await db.execute(
        "UPDATE reminders SET trigger_time = ? WHERE id = ?",
        (next_trigger, reminder_id)
    )
    await db.commit()


async def get_user_reminders(user_id: str, status: str = "pending") -> List[Dict[str, Any]]:
    """获取用户的所有指定状态提醒。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, content, trigger_time, repeat_type, original_msg
           FROM reminders WHERE user_id = ? AND status = ?
           ORDER BY trigger_time ASC""",
        (str(user_id), status)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def cancel_reminder(user_id: str, reminder_id: int) -> bool:
    """取消提醒。只能取消自己的。"""
    db = await get_db()
    cursor = await db.execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ?",
        (reminder_id, str(user_id))
    )
    await db.commit()
    return cursor.rowcount > 0


async def find_reminder_by_content(user_id: str, keyword: str) -> List[Dict[str, Any]]:
    """按关键词搜索用户的待提醒。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, content, trigger_time, repeat_type
           FROM reminders WHERE user_id = ? AND status = 'pending' AND content LIKE ?
           ORDER BY trigger_time ASC""",
        (str(user_id), f"%{keyword}%")
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


# ---------- session state (记忆持久化) ----------
async def save_session_state(session_id: str, topic: str = "", emotion: str = "",
                             context_summary: str = "", bot_mood: str = "{}"):
    """保存会话状态（启停时持久化）。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO session_state (session_id, last_topic, last_emotion, last_interaction, context_summary, bot_mood_snapshot)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(session_id) DO UPDATE SET
           last_topic = ?, last_emotion = ?, last_interaction = ?, context_summary = ?, bot_mood_snapshot = ?""",
        (session_id, topic, emotion, now, context_summary, bot_mood,
         topic, emotion, now, context_summary, bot_mood)
    )
    await db.commit()


async def get_session_state(session_id: str) -> Optional[Dict[str, Any]]:
    """获取会话状态。"""
    db = await get_db()
    async with db.execute(
        "SELECT last_topic, last_emotion, last_interaction, context_summary, bot_mood_snapshot FROM session_state WHERE session_id = ?",
        (session_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "last_topic": row["last_topic"],
            "last_emotion": row["last_emotion"],
            "last_interaction": row["last_interaction"],
            "context_summary": row["context_summary"],
            "bot_mood_snapshot": row["bot_mood_snapshot"],
        }


async def get_active_sessions(hours: float = 24.0) -> List[str]:
    """获取最近 N 小时内有交互的会话 ID。"""
    db = await get_db()
    threshold = datetime.now().timestamp() - hours * 3600
    async with db.execute(
        "SELECT session_id FROM session_state WHERE last_interaction > ?",
        (threshold,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["session_id"] for r in rows]


# ---------- user_preferences（功能③：用户偏好自学习）----------
async def get_user_preferences(user_id: str) -> Dict[str, Dict[str, float]]:
    """获取用户所有偏好，返回 {pref_type: {pref_key: pref_value}}。"""
    db = await get_db()
    result: Dict[str, Dict[str, float]] = {}
    async with db.execute(
        "SELECT pref_type, pref_key, pref_value, sample_count FROM user_preferences WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()
        for r in rows:
            ptype = r["pref_type"]
            if ptype not in result:
                result[ptype] = {}
            result[ptype][r["pref_key"]] = r["pref_value"]
    return result


async def get_top_preference(user_id: str, pref_type: str) -> Optional[str]:
    """获取某类型下得分最高的偏好 key。"""
    db = await get_db()
    async with db.execute(
        """SELECT pref_key, pref_value FROM user_preferences
           WHERE user_id = ? AND pref_type = ?
           ORDER BY pref_value DESC LIMIT 1""",
        (str(user_id), pref_type)
    ) as cursor:
        row = await cursor.fetchone()
        return row["pref_key"] if row else None


async def update_user_preference(user_id: str, pref_type: str, pref_key: str,
                                  delta: float = 0.1):
    """更新用户偏好值（累加 delta，上限 1.0）。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO user_preferences (user_id, pref_type, pref_key, pref_value, sample_count, last_updated)
           VALUES (?, ?, ?, ?, 1, ?)
           ON CONFLICT(user_id, pref_type, pref_key)
           DO UPDATE SET pref_value = MIN(1.0, pref_value + ?),
                         sample_count = sample_count + 1,
                         last_updated = ?""",
        (str(user_id), pref_type, pref_key, max(0, delta), now, delta, now)
    )
    await db.commit()


# ---------- reply_quality（功能⑦：回复质量评估）----------
async def save_reply_quality(user_id: str, session_id: str, reply_text: str,
                              quality_score: float, feedback_type: str,
                              emotion_at_reply: str = "", params_used: str = "{}"):
    """保存回复质量评估记录。"""
    db = await get_db()
    await db.execute(
        """INSERT INTO reply_quality
           (user_id, session_id, reply_text, quality_score, feedback_type, created_at, emotion_at_reply, params_used)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (str(user_id), session_id, reply_text[:500], quality_score, feedback_type,
         datetime.now().timestamp(), emotion_at_reply, params_used)
    )
    await db.commit()


async def get_quality_stats(user_id: str, days: int = 7) -> Dict[str, Any]:
    """获取用户近 N 天的回复质量统计。"""
    db = await get_db()
    threshold = datetime.now().timestamp() - days * 86400
    async with db.execute(
        """SELECT quality_score, feedback_type FROM reply_quality
           WHERE user_id = ? AND created_at > ?""",
        (str(user_id), threshold)
    ) as cursor:
        rows = await cursor.fetchall()
    if not rows:
        return {"avg_score": 0, "total": 0, "confusion_rate": 0, "rejection_rate": 0, "positive_rate": 0}
    scores = [r["quality_score"] for r in rows]
    total = len(rows)
    confusion = sum(1 for r in rows if r["feedback_type"] == "confusion")
    rejection = sum(1 for r in rows if r["feedback_type"] == "rejection")
    positive = sum(1 for r in rows if r["feedback_type"] in ("emoji_reaction", "topic_continuation"))
    return {
        "avg_score": sum(scores) / total,
        "total": total,
        "confusion_rate": confusion / total,
        "rejection_rate": rejection / total,
        "positive_rate": positive / total,
    }
