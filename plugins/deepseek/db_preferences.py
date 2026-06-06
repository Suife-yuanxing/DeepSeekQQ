"""user_preferences + reply_quality 表操作。"""
from datetime import datetime
from typing import Dict, Any, Optional

from .db_core import get_db


# ---------- user_preferences ----------
async def get_user_preferences(user_id: str) -> Dict[str, Dict[str, float]]:
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
    db = await get_db()
    async with db.execute(
        """SELECT pref_key, pref_value FROM user_preferences
           WHERE user_id = ? AND pref_type = ?
           ORDER BY pref_value DESC LIMIT 1""",
        (str(user_id), pref_type)
    ) as cursor:
        row = await cursor.fetchone()
        return row["pref_key"] if row else None


async def update_user_preference(user_id: str, pref_type: str, pref_key: str, delta: float = 0.1):
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


async def _update_user_preference_raw(user_id: str, pref_type: str, pref_key: str, weight: float = 0.05):
    """内部用：直接更新偏好（供 relationship_style 等调用）。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """INSERT INTO user_preferences (user_id, pref_type, pref_key, pref_value, sample_count, last_updated)
           VALUES (?, 'relationship_style', ?, ?, 1, ?)
           ON CONFLICT(user_id, pref_type, pref_key)
           DO UPDATE SET pref_value = pref_value + ?, sample_count = sample_count + 1, last_updated = ?""",
        (str(user_id), pref_key, weight, now, weight, now)
    )
    await db.commit()


# ---------- reply_quality ----------
async def save_reply_quality(user_id: str, session_id: str, reply_text: str,
                             quality_score: float, feedback_type: str,
                             emotion_at_reply: str = "", params_used: str = "{}"):
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
