"""memories 表操作 — 对话记忆存储与检索。"""
import aiosqlite
from typing import List, Dict, Any
from datetime import datetime

from .db_core import get_db


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


async def has_recent_message(session_id: str, minutes: int = 30) -> bool:
    """检查该 session 最近 N 分钟内是否有用户消息。"""
    cutoff = datetime.now().timestamp() - minutes * 60
    db = await get_db()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM memories
           WHERE session_id = ? AND role = 'user' AND timestamp > ?""",
        (session_id, cutoff)
    ) as cursor:
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) > 0


async def has_user_message_today(session_id: str) -> bool:
    """检查该 session 今天是否有用户消息。"""
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
