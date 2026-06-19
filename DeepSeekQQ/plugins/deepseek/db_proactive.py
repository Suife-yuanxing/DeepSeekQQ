"""proactive_log 表操作 — 主动消息日志与沉默检测 + 早安跳过状态持久化。"""
from datetime import datetime
from typing import List
from typing import Optional

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


# ---------- 早安跳过状态持久化（真人化Q3）----------

async def get_morning_skip_state(user_id: str) -> dict:
    """获取用户的早安跳过状态。

    Returns:
        {"consecutive_skips": int, "last_morning_date": str}
    """
    db = await get_db()
    try:
        async with db.execute(
            "SELECT consecutive_skips, last_morning_date FROM morning_skip_state WHERE user_id = ?",
            (str(user_id),)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "consecutive_skips": row["consecutive_skips"],
                    "last_morning_date": row["last_morning_date"] or "",
                }
            return {"consecutive_skips": 0, "last_morning_date": ""}
    except Exception:
        return {"consecutive_skips": 0, "last_morning_date": ""}


async def set_morning_skip_state(user_id: str, consecutive_skips: int, last_morning_date: str = ""):
    """设置用户的早安跳过状态（upsert）。"""
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO morning_skip_state (user_id, consecutive_skips, last_morning_date)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               consecutive_skips=excluded.consecutive_skips,
               last_morning_date=excluded.last_morning_date""",
            (str(user_id), consecutive_skips, last_morning_date)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ---------- 微事件发送历史（真人化 P2-1）----------

_MICRO_EVENT_COOLDOWN_SEC = 86400 * 30  # 30 天冷却


async def save_micro_event_sent(user_id: str, event_text: str) -> bool:
    """保存微事件发送记录到 DB。

    Args:
        user_id: 用户 ID
        event_text: 微事件文本（取前 10 字作为 event_key）

    Returns:
        是否保存成功
    """
    event_key = event_text[:10]
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO micro_event_log (user_id, event_key, event_text, sent_at) "
            "VALUES (?, ?, ?, unixepoch())",
            (str(user_id), event_key, event_text)
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        return False


async def is_micro_event_in_cooldown(user_id: str, event_text: str) -> bool:
    """检查微事件对该用户是否在 30 天冷却期内。

    Args:
        user_id: 用户 ID
        event_text: 微事件文本（取前 10 字作为 event_key）

    Returns:
        True 表示在冷却期内，不应重复发送
    """
    event_key = event_text[:10]
    db = await get_db()
    try:
        cutoff = datetime.now().timestamp() - _MICRO_EVENT_COOLDOWN_SEC
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM micro_event_log "
            "WHERE user_id = ? AND event_key = ? AND sent_at > ?",
            (str(user_id), event_key, cutoff)
        ) as cursor:
            row = await cursor.fetchone()
            return (row["cnt"] if row else 0) > 0
    except Exception:
        return False


async def cleanup_micro_event_log(days: int = 90) -> int:
    """清理超过指定天数的微事件记录。

    Args:
        days: 保留天数（默认 90 天）

    Returns:
        删除的记录数
    """
    db = await get_db()
    try:
        cutoff = datetime.now().timestamp() - (86400 * days)
        cursor = await db.execute(
            "DELETE FROM micro_event_log WHERE sent_at < ?", (cutoff,)
        )
        await db.commit()
        return cursor.rowcount
    except Exception:
        await db.rollback()
        return 0


async def get_micro_event_history(user_id: str) -> list:
    """获取用户的所有微事件发送历史。

    Returns:
        [(event_key, event_text, sent_at), ...]
    """
    db = await get_db()
    try:
        async with db.execute(
            "SELECT event_key, event_text, sent_at FROM micro_event_log "
            "WHERE user_id = ? ORDER BY sent_at DESC LIMIT 200",
            (str(user_id),)
        ) as cursor:
            rows = await cursor.fetchall()
            return [(r["event_key"], r["event_text"], r["sent_at"]) for r in rows] if rows else []
    except Exception:
        return []


# ---------- 疲劳基线操作（真人化 P2-2）----------

async def get_fatigue_baseline(user_id: str) -> dict:
    """获取用户的回复风格基线。

    Returns:
        {"sample_count": int, "avg_reply_length": float, "avg_reply_gap": float,
         "sticker_rate": float, "question_rate": float, "last_updated": float}
        如果无基线，所有值均为 0
    """
    db = await get_db()
    try:
        async with db.execute(
            "SELECT * FROM fatigue_baselines WHERE user_id = ?",
            (str(user_id),)
        ) as cursor:
            row = await cursor.fetchone()
            if row:
                return {
                    "sample_count": row["sample_count"],
                    "avg_reply_length": row["avg_reply_length"],
                    "avg_reply_gap": row["avg_reply_gap"],
                    "sticker_rate": row["sticker_rate"],
                    "question_rate": row["question_rate"],
                    "last_updated": row["last_updated"],
                }
            return {"sample_count": 0, "avg_reply_length": 0, "avg_reply_gap": 0,
                    "sticker_rate": 0, "question_rate": 0, "last_updated": 0}
    except Exception:
        return {"sample_count": 0, "avg_reply_length": 0, "avg_reply_gap": 0,
                "sticker_rate": 0, "question_rate": 0, "last_updated": 0}


async def update_fatigue_baseline(
    user_id: str, avg_length: float, avg_gap: float,
    sticker_rate: float, question_rate: float
) -> bool:
    """更新用户的回复风格基线（增量 EMA 平滑）。

    使用指数移动平均：新基线 = 0.7×旧基线 + 0.3×新样本
    """
    import time as _t
    db = await get_db()
    try:
        old = await get_fatigue_baseline(user_id)
        new_count = old["sample_count"] + 1

        if old["sample_count"] == 0:
            new_len = avg_length
            new_gap = avg_gap
            new_sticker = sticker_rate
            new_question = question_rate
        else:
            new_len = 0.7 * old["avg_reply_length"] + 0.3 * avg_length
            new_gap = 0.7 * old["avg_reply_gap"] + 0.3 * avg_gap
            new_sticker = 0.7 * old["sticker_rate"] + 0.3 * sticker_rate
            new_question = 0.7 * old["question_rate"] + 0.3 * question_rate

        await db.execute(
            """INSERT INTO fatigue_baselines (user_id, sample_count, avg_reply_length,
               avg_reply_gap, sticker_rate, question_rate, last_updated)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               sample_count=excluded.sample_count,
               avg_reply_length=excluded.avg_reply_length,
               avg_reply_gap=excluded.avg_reply_gap,
               sticker_rate=excluded.sticker_rate,
               question_rate=excluded.question_rate,
               last_updated=excluded.last_updated""",
            (str(user_id), new_count, new_len, new_gap, new_sticker, new_question, _t.time())
        )
        await db.commit()
        return True
    except Exception:
        await db.rollback()
        return False
