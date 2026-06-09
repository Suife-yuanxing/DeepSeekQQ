"""affection 表操作 — 好感度、等级、里程碑、衰减。"""
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .config import AFFECTION_LEVELS
from .db_core import get_db


async def get_affection(user_id: str) -> Dict[str, Any]:
    db = await get_db()
    async with db.execute(
        "SELECT score, level, title, total_chats, streak_days, first_interaction FROM affection WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return {"score": 0, "level": 1, "title": "陌生人", "total_chats": 0, "streak_days": 0, "first_interaction": 0}
        return {
            "score": row["score"],
            "level": row["level"],
            "title": row["title"],
            "total_chats": row["total_chats"],
            "streak_days": row["streak_days"],
            "first_interaction": row["first_interaction"] or 0,
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


async def decay_affection(inactive_days: int = 7, decay_points: float = -1.0):
    """对长期不活跃用户的好感度做自然衰减。"""
    from datetime import datetime
    db = await get_db()
    threshold = datetime.now().timestamp() - inactive_days * 86400
    # 先查出活跃用户集合，再更新非活跃用户（避免 NOT IN 全表扫描）
    async with db.execute(
        """SELECT DISTINCT REPLACE(session_id, 'private_', '') as uid FROM memories
           WHERE session_id LIKE 'private_%' AND timestamp > ?""",
        (threshold,)
    ) as cursor:
        active_rows = await cursor.fetchall()
    active_ids = {r["uid"] for r in active_rows}

    # 获取所有有好感度的用户
    async with db.execute("SELECT user_id FROM affection WHERE score > 0") as cursor:
        all_rows = await cursor.fetchall()

    # 衰减非活跃用户
    affected = 0
    for row in all_rows:
        if row["user_id"] not in active_ids:
            await db.execute(
                "UPDATE affection SET score = MAX(0, score + ?) WHERE user_id = ?",
                (decay_points, row["user_id"])
            )
            affected += 1
    if affected > 0:
        await db.commit()
        logger.info(f"[好感度] {affected} 个用户好感度自然衰减")


async def get_affection_decay_hint(user_id: str) -> str:
    """用户回归时返回好感度衰减提示。"""
    import time as _time
    db = await get_db()
    now = _time.time()
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


async def _get_affection_stats(user_id: str) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT score, level, title, total_chats, streak_days, first_interaction FROM affection WHERE user_id = ?",
        (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return dict(row)


async def check_and_trigger_milestone(user_id: str) -> Optional[str]:
    """检查是否触发关系里程碑，返回庆祝提示文本。"""
    db = await get_db()
    now = datetime.now().timestamp()

    aff = await _get_affection_stats(user_id)
    if not aff:
        return None

    score = aff.get("score", 0)
    total_chats = aff.get("total_chats", 0)
    streak = aff.get("streak_days", 0)

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
        async with db.execute(
            "SELECT 1 FROM relationship_milestones WHERE user_id = ? AND milestone_type = ?",
            (str(user_id), key)
        ) as cursor:
            if await cursor.fetchone():
                continue
        await db.execute(
            "INSERT INTO relationship_milestones (user_id, milestone_type, milestone_value, triggered_at, triggered) VALUES (?, ?, ?, ?, 1)",
            (str(user_id), key, int(info["check"]), now)
        )
        await db.commit()
        logger.info(f"[里程碑] user={user_id[:6]} 触发: {key}")
        return info["text"]
    return None
