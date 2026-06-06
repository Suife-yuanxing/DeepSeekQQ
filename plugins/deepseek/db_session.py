"""session_state + user_profiles 表操作 — 会话状态持久化与用户画像。"""
from datetime import datetime
from typing import Optional, Dict, Any, List

from nonebot import logger
from .db_core import get_db


# ---------- session_state ----------
async def save_session_state(session_id: str, topic: str = "", emotion: str = "",
                             context_summary: str = "", bot_mood: str = "{}"):
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
    db = await get_db()
    threshold = datetime.now().timestamp() - hours * 3600
    async with db.execute(
        "SELECT session_id FROM session_state WHERE last_interaction > ?",
        (threshold,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["session_id"] for r in rows]


async def get_last_conversation_context(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户最近一次对话的上下文摘要。"""
    from .db_tags import get_relevant_memory_tags
    session_id = f"private_{user_id}"
    try:
        state = await get_session_state(session_id)
        if not state or not state.get("last_topic"):
            return None

        last_interaction = state.get("last_interaction", 0)
        if last_interaction == 0:
            return None

        hours_ago = (datetime.now().timestamp() - last_interaction) / 3600
        if hours_ago > 72:
            return None

        topic = state.get("last_topic", "")
        summary = state.get("context_summary", "")

        tags = []
        try:
            tag_rows = await get_relevant_memory_tags(user_id, limit=3)
            tags = [r["content"] for r in tag_rows if r["tag_type"] in ("preference", "fact")]
        except Exception:
            pass

        return {
            "topic": topic,
            "summary": summary[:150],
            "tags": tags,
            "hours_ago": hours_ago,
        }
    except Exception as e:
        logger.debug(f"[数据库] get_last_conversation_context 失败: {e}")
        return None


# ---------- memory_summaries ----------
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


# ---------- user_profiles ----------
async def get_or_create_user_profile(user_id: str) -> Dict[str, Any]:
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
    from .db_preferences import _update_user_preference_raw
    try:
        await _update_user_preference_raw(user_id, "relationship_style", style, weight)
    except Exception:
        pass


async def get_relationship_style(user_id: str) -> Optional[str]:
    from .db_preferences import get_top_preference
    return await get_top_preference(user_id, "relationship_style")


# ---------- bot 自我披露 ----------
async def get_undisclosed_facts(user_id: str, affection_score: float) -> Optional[Dict[str, Any]]:
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
        async with db.execute(
            "SELECT 1 FROM bot_disclosures WHERE user_id = ? AND disclosure_key = ?",
            (str(user_id), key)
        ) as cursor:
            if await cursor.fetchone():
                continue
        return {"key": key, "text": info["text"]}
    return None


async def mark_disclosed(user_id: str, disclosure_key: str):
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
