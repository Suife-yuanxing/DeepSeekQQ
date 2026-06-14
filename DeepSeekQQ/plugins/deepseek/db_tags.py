"""memory_tags 表操作 — 记忆标签的增删改查、衰减、清理。"""
from datetime import datetime
from typing import Dict
from typing import List
from typing import Optional

import aiosqlite
from nonebot import logger

from .db_core import get_db


async def save_memory_tags(user_id: str, tags: List[Dict[str, str]], new_embeddings: Optional[Dict[str, bytes]] = None):
    """保存记忆标签，使用置信度评分系统。

    P0-11: 最新优先策略 — 语义相似(old, new) > 0.6 时，降级旧记录(confidence→0.1) + 写入新记录。
    人的观点会变，最新信息优先级最高。
    """
    db = await get_db()
    now = datetime.now().timestamp()

    # P0-11: 收集所有新标签内容用于语义去重
    new_contents = [tag.get("content", "").strip() for tag in tags if tag.get("content", "").strip()]

    # 获取同一用户所有同类标签的 content，用于 keyword 快速去重
    all_existing = {}
    if new_contents:
        async with db.execute(
            "SELECT id, tag_type, content, confidence FROM memory_tags WHERE user_id = ? AND confidence >= 0.1",
            (str(user_id),)
        ) as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                key = (row["tag_type"], row["content"])
                all_existing[key] = {"id": row["id"], "confidence": row["confidence"]}

    saved_ids = []
    for tag in tags:
        t_type = tag.get("type", "fact")
        content_text = tag.get("content", "").strip()
        if not content_text or len(content_text) > 200:
            continue

        # P0-11: 检查是否存在语义相近的旧记录（关键词 Jaccard 相似度 > 0.6）
        deprecated_old = False
        new_embedding = (new_embeddings or {}).get(content_text)

        for (et, ec), einfo in all_existing.items():
            if et == t_type and ec != content_text:
                # 关键词 Jaccard 快速筛
                if _keyword_jaccard(content_text, ec) > 0.6:
                    # 降级旧记录
                    await db.execute(
                        "UPDATE memory_tags SET confidence = 0.1, tier = 'short_term' WHERE id = ?",
                        (einfo["id"],)
                    )
                    logger.info(
                        f"[记忆P0-11] 降级旧记录(id={einfo['id']}, content={ec[:30]}...)"
                        f" → 新记录优先: {content_text[:30]}..."
                    )
                    deprecated_old = True

        # P0-11: 如果新标签降级了旧记录，给新标签稍高初始置信度（0.6 vs 0.5）
        new_conf = 0.6 if deprecated_old else 0.5
        new_tier = "short_term"

        async with db.execute(
            "SELECT id, confidence, hit_count FROM memory_tags WHERE user_id = ? AND tag_type = ? AND content = ?",
            (str(user_id), t_type, content_text)
        ) as cursor:
            existing = await cursor.fetchone()

        if existing:
            new_total_conf = min(0.95, max(existing["confidence"], new_conf) + 0.1)
            new_hits = existing["hit_count"] + 1
            tier = "long_term" if (new_total_conf >= 0.7 and new_hits >= 3) else "short_term"
            await db.execute(
                """UPDATE memory_tags SET weight = weight + 0.2,
                   confidence = ?, hit_count = ?, tier = ?, last_used = ?
                   WHERE id = ?""",
                (new_total_conf, new_hits, tier, now, existing["id"])
            )
            saved_ids.append(existing["id"])
        else:
            cursor = await db.execute(
                """INSERT INTO memory_tags (user_id, tag_type, content, weight, confidence, hit_count, tier, created_at, last_used)
                   VALUES (?, ?, ?, 1.0, ?, 0, ?, ?, ?)""",
                (str(user_id), t_type, content_text, new_conf, new_tier, now, now)
            )
            saved_ids.append(cursor.lastrowid)

    await db.commit()
    return saved_ids


def _keyword_jaccard(text_a: str, text_b: str) -> float:
    """计算两个文本的关键词 Jaccard 相似度（快速近似，不依赖 embedding）。

    提取 CJK 2-gram 和英文单词作为关键词集合。
    """
    import re

    def _ngrams(s: str) -> set:
        # CJK 2-gram
        cjk_chars = re.findall(r'[一-鿿㐀-䶿]', s)
        bigrams = set()
        for i in range(len(cjk_chars) - 1):
            bigrams.add(cjk_chars[i] + cjk_chars[i + 1])
        # 英文单词
        en_words = set(w.lower() for w in re.findall(r'[a-zA-Z]{2,}', s))
        bigrams.update(en_words)
        # 数字
        nums = set(re.findall(r'\d+', s))
        bigrams.update(nums)
        return bigrams

    set_a = _ngrams(text_a)
    set_b = _ngrams(text_b)

    if not set_a and not set_b:
        return 0.0
    if not set_a or not set_b:
        return 0.0

    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


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
    """获取相关记忆标签，按 置信度×权重 综合排序。

    HF-3: 将 'pinned' 类型加入查询，确保固定记忆可见。
    """
    db = await get_db()
    async with db.execute(
        """SELECT tag_type, content, weight, confidence, hit_count, last_used
           FROM memory_tags
           WHERE user_id = ? AND confidence >= 0.15
             AND (tag_type IN ('preference', 'fact', 'taboo', 'pinned') OR weight > 1.2)
           ORDER BY (confidence * weight) DESC, last_used DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        return await cursor.fetchall()


async def ensure_tag(user_id: str, tag_type: str, content: str, confidence: float = 0.5):
    """确保指定标签存在，若已存在则更新置信度。

    HF-3: 新增函数，用于 pin_memory（memory_tier.py）等场景。
    替代之前不存在的 import，修复 ImportError。

    Args:
        user_id: 用户 ID
        tag_type: 标签类型（如 'pinned', 'fact', 'preference'）
        content: 标签内容
        confidence: 置信度（默认 0.5，pinned 场景建议 0.9）
    """
    db = await get_db()
    now = datetime.now().timestamp()

    async with db.execute(
        "SELECT id, confidence FROM memory_tags WHERE user_id = ? AND tag_type = ? AND content = ?",
        (str(user_id), tag_type, content)
    ) as cursor:
        existing = await cursor.fetchone()

    if existing:
        new_conf = max(existing["confidence"], confidence)
        await db.execute(
            "UPDATE memory_tags SET confidence = ?, last_used = ? WHERE id = ?",
            (new_conf, now, existing["id"])
        )
        logger.debug(f"[db_tags] ensure_tag 更新: {tag_type}/{content[:30]} → conf={new_conf}")
    else:
        await db.execute(
            """INSERT INTO memory_tags (user_id, tag_type, content, weight, confidence, hit_count, tier, created_at, last_used)
               VALUES (?, ?, ?, 1.0, ?, 0, 'short_term', ?, ?)""",
            (str(user_id), tag_type, content, confidence, now, now)
        )
        logger.info(f"[db_tags] ensure_tag 新建: {tag_type}/{content[:30]} → conf={confidence}")

    await db.commit()


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
