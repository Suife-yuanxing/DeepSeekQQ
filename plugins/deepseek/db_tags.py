"""memory_tags 表操作 — 记忆标签的增删改查、衰减、清理。"""
from datetime import datetime
from typing import Dict
from typing import List

import aiosqlite
from nonebot import logger

from .db_core import get_db


async def save_memory_tags(user_id: str, tags: List[Dict[str, str]]):
    """保存记忆标签，使用置信度评分系统。"""
    db = await get_db()
    now = datetime.now().timestamp()
    for tag in tags:
        t_type = tag.get("type", "fact")
        content_text = tag.get("content", "").strip()
        if not content_text or len(content_text) > 200:
            continue
        async with db.execute(
            "SELECT confidence, hit_count FROM memory_tags WHERE user_id = ? AND tag_type = ? AND content = ?",
            (str(user_id), t_type, content_text)
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            new_conf = min(0.95, existing["confidence"] + 0.1)
            new_hits = existing["hit_count"] + 1
            tier = "long_term" if (new_conf >= 0.7 and new_hits >= 3) else "short_term"
            await db.execute(
                """UPDATE memory_tags SET weight = weight + 0.2,
                   confidence = ?, hit_count = ?, tier = ?, last_used = ?
                   WHERE user_id = ? AND tag_type = ? AND content = ?""",
                (new_conf, new_hits, tier, now, str(user_id), t_type, content_text)
            )
        else:
            await db.execute(
                """INSERT INTO memory_tags (user_id, tag_type, content, weight, confidence, hit_count, tier, created_at, last_used)
                   VALUES (?, ?, ?, 1.0, 0.5, 0, 'short_term', ?, ?)""",
                (str(user_id), t_type, content_text, now, now)
            )
    await db.commit()


async def decay_memory_tags(user_id: str = None, decay_rate: float = 0.02, tier: str = None):
    """对记忆标签做时间衰减。"""
    db = await get_db()
    now = datetime.now().timestamp()
    params: list = [decay_rate, now]

    if user_id:
        if tier:
            query = """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
                   WHERE user_id = ? AND last_used < ? - 86400 AND tier = ?"""
            params.extend([str(user_id), tier])
        else:
            query = """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
                   WHERE user_id = ? AND last_used < ? - 86400"""
            params.append(str(user_id))
    else:
        if tier:
            query = """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
                   WHERE last_used < ? - 86400 AND tier = ?"""
            params.append(tier)
        else:
            query = """UPDATE memory_tags SET confidence = MAX(0.0, confidence - ?)
                   WHERE last_used < ? - 86400"""
    await db.execute(query, params)
    await db.commit()


async def prune_memory_tags(min_confidence: float = 0.15, tier: str = None):
    """清理置信度过低的记忆标签。"""
    db = await get_db()
    if tier:
        cursor = await db.execute(
            "DELETE FROM memory_tags WHERE confidence < ? AND tier = ?",
            (min_confidence, tier)
        )
    else:
        cursor = await db.execute(
            "DELETE FROM memory_tags WHERE confidence < ?", (min_confidence,)
        )
    await db.commit()
    deleted = cursor.rowcount
    if deleted > 0:
        logger.info(f"[记忆] 清理了 {deleted} 条低置信度标签 (tier={tier or 'all'})")
    return deleted


async def get_relevant_memory_tags(user_id: str, limit: int = 5) -> List[aiosqlite.Row]:
    """获取相关记忆标签，按 置信度×权重 综合排序。"""
    db = await get_db()
    async with db.execute(
        """SELECT tag_type, content, weight, confidence, hit_count, last_used
           FROM memory_tags
           WHERE user_id = ? AND confidence >= 0.15
             AND (tag_type IN ('preference', 'fact', 'taboo') OR weight > 1.2)
           ORDER BY (confidence * weight) DESC, last_used DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        return await cursor.fetchall()


async def boost_memory_tag(user_id: str, content: str, boost: float = 0.1):
    """当记忆被成功引用时，提升其置信度和权重。"""
    db = await get_db()
    now = datetime.now().timestamp()
    await db.execute(
        """UPDATE memory_tags SET confidence = MIN(0.95, confidence + ?),
               weight = weight + 0.05, last_used = ?
           WHERE user_id = ? AND content = ?""",
        (boost, now, str(user_id), content)
    )
    await db.commit()


async def get_all_memory_tags_for_user(user_id: str) -> List[dict]:
    """获取用户的所有记忆标签，用于向量索引初始化。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, tag_type, content, weight, confidence, hit_count, last_used
           FROM memory_tags
           WHERE user_id = ? AND confidence >= 0.15
           ORDER BY (confidence * weight) DESC""",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]
