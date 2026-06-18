"""意见追踪 — 记录bot表达过的立场，防止前后矛盾。

- 每对(user, topic)只保留一条记录（upsert语义）
- 高好感度用户（>=500）多次讨论同一话题可以微调立场
- 查询历史立场，注入prompt保持一致性
"""
import time
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


async def record_opinion(
    user_id: str,
    topic: str,
    bot_stance: str,
    user_stance: str = "",
    agreement_level: str = "neutral",
) -> bool:
    """记录bot在一个话题上表达的立场（upsert）。"""
    try:
        from .database import get_db
        db = await get_db()

        now = time.time()
        await db.execute(
            """INSERT INTO opinion_memory (user_id, topic, bot_stance, user_stance,
               agreement_level, created_at, last_mentioned_at, mention_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1)
               ON CONFLICT(user_id, topic) DO UPDATE SET
               bot_stance = excluded.bot_stance,
               user_stance = excluded.user_stance,
               agreement_level = excluded.agreement_level,
               last_mentioned_at = excluded.last_mentioned_at,
               mention_count = opinion_memory.mention_count + 1""",
            (user_id, topic, bot_stance, user_stance, agreement_level, now, now)
        )
        await db.commit()
        logger.debug(f"[意见追踪] 记录立场: topic={topic} user={user_id[:8]}")
        return True
    except Exception as e:
        logger.error(f"[意见追踪] 记录失败: {e}")
        return False


async def get_past_opinions(user_id: str, limit: int = 5) -> List[dict]:
    """获取bot对这个用户最近表达过的立场，防止前后矛盾。

    Returns:
        [{topic, bot_stance, user_stance, agreement_level, mention_count, last_mentioned_at}, ...]
    """
    try:
        from .database import get_db
        db = await get_db()

        async with db.execute(
            "SELECT topic, bot_stance, user_stance, agreement_level, mention_count, "
            "last_mentioned_at FROM opinion_memory WHERE user_id = ? "
            "ORDER BY last_mentioned_at DESC LIMIT ?",
            (user_id, limit)
        ) as cursor:
            rows = await cursor.fetchall()

        return [dict(r) for r in rows]
    except Exception as e:
        logger.debug(f"[意见追踪] 查询历史立场失败（非关键）: {e}")
        return []


async def get_topic_opinion(user_id: str, topic: str) -> Optional[dict]:
    """获取bot在特定话题上对这个用户表达过的立场。"""
    try:
        from .database import get_db
        db = await get_db()

        async with db.execute(
            "SELECT topic, bot_stance, user_stance, agreement_level, mention_count "
            "FROM opinion_memory WHERE user_id = ? AND topic = ?",
            (user_id, topic)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
    except Exception:
        return None


async def evolve_opinion(
    user_id: str,
    topic: str,
    new_stance: str,
    affection_score: float = 0,
) -> bool:
    """高好感度用户可能影响bot的观点微调。

    条件：affection >= 500 且 该话题已被讨论过 >= 3次
    """
    from .constants import AFFECTION_CLOSE

    if affection_score < AFFECTION_CLOSE:
        return False

    try:
        existing = await get_topic_opinion(user_id, topic)
        if not existing or existing.get("mention_count", 0) < 3:
            return False

        # 只做微调，不彻底反转
        await record_opinion(
            user_id=user_id,
            topic=topic,
            bot_stance=new_stance,
            agreement_level="evolved",
        )
        logger.info(f"[意见演化] topic={topic} 立场微调: user={user_id[:8]}")
        return True
    except Exception as e:
        logger.error(f"[意见演化] 失败: {e}")
        return False


def build_past_opinions_hint(past_opinions: List[dict]) -> str:
    """将历史立场列表转换为prompt提示文本。

    Returns:
        注入prompt的文本，或空字符串
    """
    if not past_opinions:
        return ""

    lines = ["【你之前表达过的观点（保持立场一致）】"]
    for op in past_opinions:
        topic = op["topic"]
        stance = op["bot_stance"]
        count = op.get("mention_count", 1)
        if count >= 3:
            lines.append(f"- 关于{topic}：{stance}（讨论过多次，立场比较坚定）")
        else:
            lines.append(f"- 关于{topic}：{stance}")

    return "\n".join(lines)
