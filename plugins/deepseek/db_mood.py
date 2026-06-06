"""mood 表操作 — bot 情绪、用户情绪、猫娘心情。"""
from datetime import datetime
from typing import Dict, Any, Optional
import random

from .db_core import get_db


# ---------- catgirl_mood ----------
async def get_catgirl_mood() -> Dict[str, Any]:
    db = await get_db()
    async with db.execute("SELECT mood, score FROM catgirl_mood WHERE id = 1") as cursor:
        row = await cursor.fetchone()
        return {"mood": row["mood"], "score": row["score"]}


async def update_catgirl_mood(user_msg: str) -> Dict[str, Any]:
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


# ---------- bot_mood ----------
async def get_bot_mood() -> Dict[str, Any]:
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
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        "UPDATE bot_mood SET valence=?, arousal=?, dominant=?, trigger_reason=?, trigger_time=?, last_updated=? WHERE id=1",
        (valence, arousal, dominant, reason, now, now)
    )
    await db.commit()


# ---------- user_mood (VA 情绪模型) ----------
async def get_user_mood(user_id: str) -> Optional[Dict[str, Any]]:
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
