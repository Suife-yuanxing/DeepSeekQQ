"""mood 表操作 — bot 情绪、用户情绪、猫娘心情、情绪快照。"""
import time
from datetime import datetime
from typing import Dict, Any, Optional, List
import random

from .db_core import get_db


# ---------- catgirl_mood ----------
async def get_catgirl_mood() -> Dict[str, Any]:
    db = await get_db()
    async with db.execute("SELECT mood, score FROM catgirl_mood WHERE id = 1") as cursor:
        row = await cursor.fetchone()
        return {"mood": row["mood"], "score": row["score"]}


async def update_catgirl_mood(user_msg: str) -> Dict[str, Any]:
    happy = ["开心", "喜欢", "爱", "棒", "可爱", "喵", "亲", "抱", "摸摸", "乖", "嘿嘿", "哈哈"]
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


# ---------- mood_snapshots (情绪快照) ----------
async def save_mood_snapshot(user_id: str, session_id: str):
    """会话结束时保存情绪快照，供下次关心。"""
    mood = await get_user_mood(user_id)
    if not mood:
        return
    db = await get_db()
    now = time.time()
    await db.execute(
        """INSERT INTO mood_snapshots (user_id, session_id, valence, arousal, dominant, snapshot_time)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (str(user_id), session_id, mood["valence"], mood["arousal"], mood["dominant"], now)
    )
    await db.commit()


async def get_last_mood_snapshot(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户上一次会话结束时的情绪快照。"""
    db = await get_db()
    async with db.execute(
        """SELECT valence, arousal, dominant, snapshot_time
           FROM mood_snapshots WHERE user_id = ?
           ORDER BY snapshot_time DESC LIMIT 1""",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "valence": row["valence"],
            "arousal": row["arousal"],
            "dominant": row["dominant"],
            "snapshot_time": row["snapshot_time"],
        }


def get_mood_care_hint(snapshot: Dict[str, Any]) -> Optional[str]:
    """根据情绪快照生成关心提示（如果需要的话）。

    只对负面情绪且48小时内有效。
    30%概率触发，避免每次都关心。
    """
    if not snapshot:
        return None

    hours_since = (time.time() - snapshot["snapshot_time"]) / 3600

    # 超过48小时不关心
    if hours_since > 48:
        return None

    # 只关心负面情绪
    negative_emotions = ("难过", "生气", "担心", "害怕", "委屈", "焦虑")
    if snapshot["dominant"] not in negative_emotions:
        return None

    # 30%概率触发
    if random.random() > 0.3:
        return None

    emotion = snapshot["dominant"]
    hints = {
        "难过": "你记得他上次聊天时有点难过。如果合适的话，自然地关心一下，比如'最近好点了吗？'",
        "生气": "你记得他上次聊天时有点生气。可以委婉地问一下'那天后来怎样了？'",
        "担心": "你记得他上次聊天时有些担心。可以关心一下'那件事后来怎么样了？'",
        "害怕": "你记得他上次聊天时有点害怕。温柔地问一句'还担心那个吗？'",
        "委屈": "你记得他上次聊天时有点委屈。关心一下'最近还好吗？'",
        "焦虑": "你记得他上次聊天时有些焦虑。轻声问'最近压力大吗？'",
    }
    return hints.get(emotion, f"你记得他上次聊天时{emotion}。自然地关心一下，但不要刻意。")
