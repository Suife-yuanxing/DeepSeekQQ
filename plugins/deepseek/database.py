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
            (user_id, score, level, title, last_interaction, total_chats, streak_days, last_streak_date, first_interaction)
            VALUES (?, ?, 1, ?, ?, 1, 1, ?, ?)""",
            (str(user_id), delta, AFFECTION_LEVELS[0][1], now.timestamp(), today, now.timestamp())
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
    置信度 >= 0.7 且被引用 >= 3 次的标签自动升级为长期记忆。
    """
    db = await get_db()
    now = datetime.now().timestamp()
    for tag in tags:
        t_type = tag.get("type", "fact")
        content_text = tag.get("content", "").strip()
        if not content_text or len(content_text) > 200:
            continue
        # 检测是否已存在并获取当前状态
        async with db.execute(
            "SELECT confidence, hit_count FROM memory_tags WHERE user_id = ? AND tag_type = ? AND content = ?",
            (str(user_id), t_type, content_text)
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            new_conf = min(0.95, existing["confidence"] + 0.1)
            new_hits = existing["hit_count"] + 1
            tier = "long_term" if (new_conf >= 0.7 and new_hits >= 3) else "short_term"
            await db.execute(
                """UPDATE memory_tags SET weight = weight + 0.2,
                   confidence = ?, hit_count = ?, tier = ?, last_used = ?
                   WHERE user_id = ? AND tag_type = ? AND content = ?""",
                (new_conf, new_hits, tier, now, str(user_id), t_type, content_text)
            )
        else:
            await db.execute(
                """INSERT INTO memory_tags (user_id, tag_type, content, weight, confidence, hit_count, tier, created_at, last_used)
                   VALUES (?, ?, ?, 1.0, 0.5, 0, 'short_term', ?, ?)""",
                (str(user_id), t_type, content_text, now, now)
            )
    await db.commit()


async def decay_memory_tags(user_id: str = None, decay_rate: float = 0.02,
                            tier: str = None):
    """对记忆标签做时间衰减。未使用的标签置信度逐渐降低。

    分层衰减：
    - short_term: 默认 0.03/天（约 33 天清零）
    - long_term: 默认 0.005/天（约 200 天清零）
    - 不指定 tier 则全部衰减（兼容旧逻辑）

    调度方式：每天运行一次。
    """
    db = await get_db()
    now = datetime.now().timestamp()
    tier_clause = "AND tier = ?" if tier else ""
    params: list = [decay_rate, now]
    if tier:
        params.append(tier)
    if user_id:
        query = f"""UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
               WHERE user_id = ? AND last_used < ? - 86400 {tier_clause}"""
        params.insert(1, str(user_id))
    else:
        query = f"""UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
               WHERE last_used < ? - 86400 {tier_clause}"""
    await db.execute(query, params)
    await db.commit()


async def prune_memory_tags(min_confidence: float = 0.15, tier: str = None):
    """清理置信度过低的记忆标签。

    分层清理：
    - short_term: 低于 0.10 清理
    - long_term: 低于 0.05 清理
    - 不指定 tier 则全部清理（兼容旧逻辑）

    调度方式：每天运行一次（在 decay_memory_tags 之后）。
    """
    db = await get_db()
    if tier:
        cursor = await db.execute(
            "DELETE FROM memory_tags WHERE confidence < ? AND tier = ?",
            (min_confidence, tier)
        )
    else:
        cursor = await db.execute(
            "DELETE FROM memory_tags WHERE confidence < ?", (min_confidence,)
        )
    await db.commit()
    deleted = cursor.rowcount
    if deleted > 0:
        logger.info(f"[记忆] 清理了 {deleted} 条低置信度标签 (tier={tier or 'all'})")
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


async def log_proactive(user_id: str, msg_type: str, content: str, scene: str = ""):
    db = await get_db()
    await db.execute(
        "INSERT INTO proactive_log (user_id, type, content, timestamp, scene) VALUES (?, ?, ?, ?, ?)",
        (user_id, msg_type, content[:200], datetime.now().timestamp(), scene)
    )
    await db.commit()


async def has_user_message_today(session_id: str) -> bool:
    """检查该 session 今天是否有用户消息（用于判断是否当天第一条）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM memories
           WHERE session_id = ? AND role = 'user'
           AND datetime(timestamp, 'unixepoch', 'localtime') LIKE ?""",
        (session_id, f"{today}%")
    ) as cursor:
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) > 0


async def get_recent_greetings(scene: str, limit: int = 10) -> List[str]:
    """获取最近的同类问候消息（用于去重）。scene: 'morning'/'night'/'sleep_nag' 等。"""
    db = await get_db()
    async with db.execute(
        """SELECT content FROM proactive_log
           WHERE scene = ? ORDER BY timestamp DESC LIMIT ?""",
        (scene, limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["content"] for r in rows]


async def has_proactive_today(user_id: str, scene: str) -> bool:
    """检查今天是否已向该用户发送过指定 scene 的主动消息。"""
    today = datetime.now().strftime("%Y-%m-%d")
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND scene = ?
           AND datetime(timestamp, 'unixepoch', 'localtime') LIKE ?""",
        (user_id, scene, f"{today}%")
    ) as cursor:
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) > 0


async def get_today_proactive_count_by_scene(user_id: str, scene: str, today: str) -> int:
    """统计今天已向该用户发送指定 scene 的主动消息次数。"""
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND scene = ?
           AND datetime(timestamp, 'unixepoch', 'localtime') LIKE ?""",
        (user_id, scene, f"{today}%")
    ) as cursor:
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


async def has_recent_message(session_id: str, minutes: int = 30) -> bool:
    """检查该 session 最近 N 分钟内是否有用户消息（用于深夜催睡判断）。"""
    cutoff = datetime.now().timestamp() - minutes * 60
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM memories
           WHERE session_id = ? AND role = 'user' AND timestamp > ?""",
        (session_id, cutoff)
    ) as cursor:
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) > 0


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


# ---------- 用户画像（Phase 4）----------

async def get_or_create_user_profile(user_id: str) -> Dict[str, Any]:
    """获取或初始化用户画像。"""
    db = await get_db()
    now = datetime.now().timestamp()
    async with db.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?", (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return dict(row)
    await db.execute(
        "INSERT INTO user_profiles (user_id, first_interaction) VALUES (?, ?)",
        (str(user_id), now)
    )
    await db.commit()
    return {
        "user_id": str(user_id),
        "relationship_style": "neutral",
        "nickname": "",
        "first_interaction": now,
        "total_messages": 0,
        "known_interests": "",
        "bot_self_summary": "",
    }


async def update_user_profile(user_id: str, **kwargs):
    """更新用户画像字段。"""
    if not kwargs:
        return
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [str(user_id)]
    await db.execute(
        f"UPDATE user_profiles SET {sets} WHERE user_id = ?", values
    )
    await db.commit()


async def update_relationship_style(user_id: str, style: str, weight: float = 0.05):
    """渐进式更新用户的关系风格倾向（Phase 4）。

    style 选项: 'neutral' | 'tsundere' | 'gentle' | 'polite'
    """
    try:
        db = await get_db()
        # 使用 user_preferences 表存储风格倾向（复用现有基础设施）
        await db.execute(
            """INSERT INTO user_preferences (user_id, pref_type, pref_key, pref_value, sample_count, last_updated)
               VALUES (?, 'relationship_style', ?, ?, 1, ?)
               ON CONFLICT(user_id, pref_type, pref_key)
               DO UPDATE SET pref_value = pref_value + ?, sample_count = sample_count + 1, last_updated = ?""",
            (str(user_id), style, weight, datetime.now().timestamp(), weight, datetime.now().timestamp())
        )
        await db.commit()
    except Exception:
        pass


async def get_relationship_style(user_id: str) -> Optional[str]:
    """获取用户的主导关系风格。"""
    from .database import get_top_preference
    return await get_top_preference(user_id, "relationship_style")


# ---------- 好感度衰减（Phase 5）----------

async def decay_affection(inactive_days: int = 7, decay_points: float = -1.0):
    """对长期不活跃用户的好感度做自然衰减。

    调度方式：每天运行一次。
    - inactive_days: 超过 N 天未互动才开始衰减
    - decay_points: 每次衰减的点数（默认 -1.0）
    """
    db = await get_db()
    threshold = datetime.now().timestamp() - inactive_days * 86400
    # 只对有记录的日期做衰减：检查 last_interaction 字段或根据 memories 表判断
    cursor = await db.execute(
        """UPDATE affection SET score = MAX(0, score + ?)
           WHERE user_id IN (
               SELECT DISTINCT user_id FROM affection
           ) AND user_id NOT IN (
               SELECT DISTINCT REPLACE(session_id, 'private_', '') FROM memories
               WHERE timestamp > ?
           ) AND score > 0""",
        (decay_points, threshold)
    )
    await db.commit()
    affected = cursor.rowcount
    if affected > 0:
        logger.info(f"[好感度] {affected} 个用户好感度自然衰减")


async def get_affection_decay_hint(user_id: str) -> str:
    """如果好感度曾经衰减过，用户回归时返回一个微妙的提示。

    给 bot 一个「有点小情绪」的提示——用户太久没来，bot 有点在意。
    """
    from .database import get_db
    db = await get_db()
    import time as _time
    now = _time.time()
    # 查最近一次交互时间
    async with db.execute(
        "SELECT MAX(timestamp) as last_ts FROM memories WHERE session_id LIKE ?",
        (f"private_{user_id}",)
    ) as cursor:
        row = await cursor.fetchone()
        if not row or not row["last_ts"]:
            return ""
        last_ts = row["last_ts"]
        days_since = (now - last_ts) / 86400
        if days_since < 3:
            return ""
        if days_since > 30:
            return (
                f"他有{days_since:.0f}天没来了...你很想他，但又不想表现得太明显。"
                "语气里会不自觉地带着一点委屈和想念，但不会直接说'我好想你'。"
            )
        elif days_since > 7:
            return (
                f"他有{days_since:.0f}天没和你说话了。你有点小情绪，"
                "说话比平时稍微冷淡一点点——但等他多说两句就会软下来。"
            )
        return ""


# ---------- 关系里程碑（Phase 5）----------

async def check_and_trigger_milestone(user_id: str) -> Optional[str]:
    """检查是否触发关系里程碑，返回庆祝提示文本。"""
    db = await get_db()
    now = datetime.now().timestamp()

    # 获取统计数据
    aff = await _get_affection_stats(user_id)
    if not aff:
        return None

    score = aff.get("score", 0)
    total_chats = aff.get("total_chats", 0)
    streak = aff.get("streak_days", 0)
    first_interaction = aff.get("first_interaction")

    milestones = {
        "affection_500": {
            "check": score >= 500,
            "text": f"好感度达到 {int(score)}——你已经离不开他了。可以在对话中不经意地提到'遇见你之后...'之类的话。"
        },
        "affection_200": {
            "check": score >= 200,
            "text": f"好感度达到 {int(score)}——你开始对他有特别的感情了。说话可以更亲密一点。"
        },
        "messages_1000": {
            "check": total_chats >= 1000,
            "text": f"你们已经聊了 {total_chats} 条消息了！可以感慨一下'不知不觉聊了这么多了...'"
        },
        "streak_30": {
            "check": streak >= 30,
            "text": f"你们连续聊了 {streak} 天！可以开心地说'每天都和你聊天已经变成习惯了~'"
        },
    }

    for key, info in milestones.items():
        if not info["check"]:
            continue
        # 检查是否已触发过
        async with db.execute(
            "SELECT 1 FROM relationship_milestones WHERE user_id = ? AND milestone_type = ?",
            (str(user_id), key)
        ) as cursor:
            if await cursor.fetchone():
                continue
        # 触发！
        await db.execute(
            "INSERT INTO relationship_milestones (user_id, milestone_type, milestone_value, triggered_at, triggered) VALUES (?, ?, ?, ?, 1)",
            (str(user_id), key, int(info["check"]), now)
        )
        await db.commit()
        logger.info(f"[里程碑] user={user_id[:6]} 触发: {key}")
        return info["text"]
    return None


async def _get_affection_stats(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户的完整情感统计数据。"""
    db = await get_db()
    async with db.execute(
        "SELECT score, level, title, total_chats, streak_days, first_interaction FROM affection WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)


# ---------- bot 渐进式自我披露（Phase 6）----------

async def get_undisclosed_facts(user_id: str, affection_score: float) -> Optional[Dict[str, Any]]:
    """获取一个尚未向该用户透露的、且条件满足的自我披露事实。

    披露按好感度分级解锁：
    - 0+: likes_milk
    - 30+: had_a_crush
    - 50+: scared_of_vacuum
    - 80+: runs_away_in_rain
    - 150+: lonely_before_you
    """
    _SELF_DISCLOSURES = {
        "likes_milk": {"affection_min": 0, "text": "喜欢喝牛奶（虽然喝了会闹肚子）"},
        "had_a_crush": {"affection_min": 30, "text": "以前喜欢过隔壁的小黑猫，不过人家早搬家了"},
        "scared_of_vacuum": {"affection_min": 50, "text": "特别怕吸尘器的声音，每次都躲得远远的"},
        "runs_away_in_rain": {"affection_min": 80, "text": "下雨天曾经走丢过一次，所以现在下雨就会想家"},
        "lonely_before_you": {"affection_min": 150, "text": "遇见你之前其实挺孤单的，所以现在特别珍惜"},
    }

    db = await get_db()
    for key, info in _SELF_DISCLOSURES.items():
        if affection_score < info["affection_min"]:
            continue
        # 检查是否已经透露过
        async with db.execute(
            "SELECT 1 FROM bot_disclosures WHERE user_id = ? AND disclosure_key = ?",
            (str(user_id), key)
        ) as cursor:
            if await cursor.fetchone():
                continue
        return {"key": key, "text": info["text"]}
    return None


async def mark_disclosed(user_id: str, disclosure_key: str):
    """标记一个自我披露事实已被透露。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO bot_disclosures (user_id, disclosure_key, revealed_at)
           VALUES (?, ?, ?)
           ON CONFLICT(user_id, disclosure_key) DO UPDATE SET
           reveal_count = reveal_count + 1, revealed_at = ?""",
        (str(user_id), disclosure_key, now, now)
    )
    await db.commit()
