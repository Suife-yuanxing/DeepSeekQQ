"""proactive_log 表操作 — 主动消息日志与沉默检测。"""
from datetime import datetime
from typing import List

from .db_core import get_db


def _today_range() -> tuple:
    """返回今天的起止时间戳。"""
    now = datetime.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return start, start + 86400


async def get_today_proactive_count(user_id: str, today: str = "") -> int:
    db = await get_db()
    start, end = _today_range()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND type = 'private'
           AND timestamp >= ? AND timestamp < ?""",
        (user_id, start, end)
    ) as cursor:
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


async def log_proactive(user_id: str, msg_type: str, content: str, scene: str = ""):
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO proactive_log (user_id, type, content, timestamp, scene) VALUES (?, ?, ?, ?, ?)",
            (user_id, msg_type, content[:200], datetime.now().timestamp(), scene)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def get_recent_greetings(scene: str, limit: int = 10) -> List[str]:
    db = await get_db()
    async with db.execute(
        """SELECT content FROM proactive_log
           WHERE scene = ? ORDER BY timestamp DESC LIMIT ?""",
        (scene, limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["content"] for r in rows]


async def has_proactive_today(user_id: str, scene: str) -> bool:
    db = await get_db()
    start, end = _today_range()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND scene = ?
           AND timestamp >= ? AND timestamp < ?""",
        (user_id, scene, start, end)
    ) as cursor:
        row = await cursor.fetchone()
        return (row["cnt"] if row else 0) > 0


async def get_today_proactive_count_by_scene(user_id: str, scene: str, today: str = "") -> int:
    db = await get_db()
    start, end = _today_range()
    async with db.execute(
        """SELECT COUNT(*) as cnt FROM proactive_log
           WHERE user_id = ? AND scene = ?
           AND timestamp >= ? AND timestamp < ?""",
        (user_id, scene, start, end)
    ) as cursor:
        row = await cursor.fetchone()
        return row["cnt"] if row else 0


async def get_silent_private_users(threshold: float, limit: int = 500) -> List[str]:
    db = await get_db()
    async with db.execute(
        """SELECT session_id, MAX(timestamp) as last_time
           FROM memories WHERE session_id LIKE 'private_%' AND archived = 0
           GROUP BY session_id HAVING last_time < ?
           ORDER BY last_time ASC
           LIMIT ?""",
        (threshold, limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["session_id"].replace("private_", "") for r in rows]
