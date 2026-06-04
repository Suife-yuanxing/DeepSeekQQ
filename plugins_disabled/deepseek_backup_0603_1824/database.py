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
        CREATE TABLE IF NOT EXISTS memory_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            tag_type TEXT NOT NULL,
            content TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            created_at REAL,
            last_used REAL,
            UNIQUE(user_id, tag_type, content)
        )
    """)
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
    db = await get_db()
    now = datetime.now().timestamp()
    for tag in tags:
        t_type = tag.get("type", "fact")
        content_text = tag.get("content", "").strip()
        if not content_text or len(content_text) > 200:
            continue
        await db.execute(
            """INSERT INTO memory_tags (user_id, tag_type, content, weight, created_at, last_used)
               VALUES (?, ?, ?, 1.0, ?, ?)
               ON CONFLICT(user_id, tag_type, content)
               DO UPDATE SET weight = weight + 0.2, last_used = ?""",
            (str(user_id), t_type, content_text, now, now, now)
        )
    await db.commit()


async def get_relevant_memory_tags(user_id: str, limit: int = 5) -> List[aiosqlite.Row]:
    db = await get_db()
    now = datetime.now().timestamp()
    async with db.execute(
        """SELECT tag_type, content, weight, last_used FROM memory_tags
           WHERE user_id = ? AND (tag_type IN ('preference', 'fact', 'taboo') OR weight > 1.2)
           ORDER BY weight DESC, last_used DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        return await cursor.fetchall()


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
