"""reminders 表操作 — 备忘录/提醒 CRUD。"""
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List

from .db_core import get_db


async def save_reminder(user_id: str, session_id: str, content: str,
                        trigger_time: float, repeat_type: str = "none",
                        original_msg: str = "") -> int:
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
    db = await get_db()
    await db.execute("UPDATE reminders SET status = 'done' WHERE id = ?", (reminder_id,))
    await db.commit()


async def reschedule_reminder(reminder_id: int, next_trigger: float):
    db = await get_db()
    await db.execute(
        "UPDATE reminders SET trigger_time = ? WHERE id = ?",
        (next_trigger, reminder_id)
    )
    await db.commit()


async def get_user_reminders(user_id: str, status: str = "pending") -> List[Dict[str, Any]]:
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
    db = await get_db()
    cursor = await db.execute(
        "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_id = ?",
        (reminder_id, str(user_id))
    )
    await db.commit()
    return cursor.rowcount > 0


async def find_reminder_by_content(user_id: str, keyword: str) -> List[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        """SELECT id, content, trigger_time, repeat_type
           FROM reminders WHERE user_id = ? AND status = 'pending' AND content LIKE ?
           ORDER BY trigger_time ASC""",
        (str(user_id), f"%{keyword}%")
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
