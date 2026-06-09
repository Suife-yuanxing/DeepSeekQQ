"""记忆深化数据库操作 — 共同回忆、私人梗、重要日期。

三个新表支撑记忆系统深化：
- shared_memories: 共同经历和重要时刻
- private_memes: 私人梗、专属昵称、暗号
- important_dates: 生日、纪念日、认识日等
"""
import random
import re
import time
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .db_core import get_db

# ============================================================
# 话题关联度计算 — 提升回忆触发的相关性
# ============================================================

def calculate_topic_relevance(
    current_topic_keywords: List[str],
    memory: Dict[str, Any]
) -> float:
    """计算当前话题与回忆的关联度（0-1）"""
    memory_desc = memory.get('event_desc', '')
    memory_keywords = set(re.findall(r'[一-鿿]{2,6}', memory_desc))
    current_keywords = set(current_topic_keywords)

    if not memory_keywords or not current_keywords:
        return 0.0

    # Jaccard 相似度
    intersection = memory_keywords & current_keywords
    union = memory_keywords | current_keywords

    jaccard = len(intersection) / len(union) if union else 0.0

    # 关键词权重（情感词权重更高）
    emotional_keywords = {'开心', '难过', '生气', '喜欢', '讨厌', '第一次', '重要'}
    emotional_intersection = intersection & emotional_keywords
    emotional_boost = len(emotional_intersection) * 0.1

    return min(1.0, jaccard + emotional_boost)


# ============================================================
# 私人梗反馈循环 — 根据用户反应调整权重
# ============================================================

async def update_meme_feedback(
    meme_id: int,
    user_reaction: str  # 'positive', 'negative', 'neutral'
):
    """更新私人梗的用户反馈"""
    weights = {
        'positive': 1.2,   # 正面反应，提升权重
        'negative': 0.7,   # 负面反应，降低权重
        'neutral': 1.0     # 无反应，保持
    }

    try:
        db = await get_db()
        await db.execute("""
            UPDATE private_memes
            SET frequency = frequency * ?,
                usage_count = usage_count + 1
            WHERE id = ?
        """, (weights.get(user_reaction, 1.0), meme_id))
        await db.commit()
    except Exception as e:
        logger.debug(f"[私人梗反馈] 更新失败: {e}")


def detect_user_reaction(response_text: str) -> str:
    """检测用户对梗的反应"""
    positive_indicators = ['哈哈', '笑死', '可爱', '喜欢', '❤️', '😊', 'lol', '666']
    negative_indicators = ['尬', '无聊', '别说了', '够了', '🙄', '无语']

    text_lower = response_text.lower()

    pos_count = sum(1 for kw in positive_indicators if kw in text_lower)
    neg_count = sum(1 for kw in negative_indicators if kw in text_lower)

    if pos_count > neg_count:
        return 'positive'
    if neg_count > pos_count:
        return 'negative'
    return 'neutral'


async def get_meme_to_use(
    user_id: str,
    current_msg: str,
    context_keywords: List[str] = None
) -> Optional[Dict[str, Any]]:
    """获取适合使用的私人梗（考虑权重）"""
    try:
        db = await get_db()
        now = time.time()

        # 获取匹配的梗
        async with db.execute("""
            SELECT id, meme_type, content, trigger_keywords, frequency, usage_count, last_used
            FROM private_memes WHERE user_id = ?
            ORDER BY frequency DESC
        """, (str(user_id),)) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return None

        # 筛选可用的梗
        available = []
        for row in rows:
            row_dict = dict(row)
            keywords = row_dict.get('trigger_keywords') or ''
            if not keywords:
                continue

            # 检查关键词匹配
            keyword_list = [k.strip() for k in keywords.split(',') if k.strip()]
            matched = any(kw in current_msg for kw in keyword_list)
            if not matched:
                continue

            # 冷却检查：最近 1 小时内用过的梗不重复
            if row_dict.get('last_used'):
                hours_since = (now - row_dict['last_used']) / 3600
                if hours_since < 1:
                    continue

            available.append(row_dict)

        if not available:
            return None

        # 加权随机选择
        weights = [max(0.1, m.get('frequency', 0.3)) for m in available]
        total_weight = sum(weights)

        if total_weight == 0:
            return None

        # 按权重随机选择
        r = random.random() * total_weight
        cumulative = 0
        for meme, weight in zip(available, weights):
            cumulative += weight
            if r <= cumulative:
                # 更新使用记录
                await db.execute("""
                    UPDATE private_memes
                    SET usage_count = usage_count + 1, last_used = ?
                    WHERE id = ?
                """, (now, meme['id']))
                await db.commit()
                return meme

        return available[-1] if available else None

    except Exception as e:
        logger.debug(f"[私人梗选择] 失败: {e}")
        return None


# ============================================================
# 共同回忆 (shared_memories)
# ============================================================

async def save_shared_memory(
    user_id: str,
    event_type: str,
    event_desc: str,
    emotion_tag: str = "",
    context: str = "",
    importance: float = 0.5,
):
    """保存一条共同回忆。

    event_type: first_chat / shared_experience / funny_milestone /
                emotional_moment / important_event
    """
    db = await get_db()
    now = datetime.now().timestamp()
    # 去重：同一用户、同一类型、描述相似则合并（提升 importance）
    async with db.execute(
        "SELECT id, importance FROM shared_memories WHERE user_id = ? AND event_type = ? AND event_desc = ?",
        (str(user_id), event_type, event_desc[:200])
    ) as cursor:
        existing = await cursor.fetchone()
    if existing:
        new_imp = min(1.0, existing["importance"] + 0.1)
        await db.execute(
            "UPDATE shared_memories SET importance = ?, recall_count = recall_count + 1, last_recalled = ? WHERE id = ?",
            (new_imp, now, existing["id"])
        )
    else:
        await db.execute(
            """INSERT INTO shared_memories
               (user_id, event_type, event_desc, emotion_tag, context, importance, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(user_id), event_type, event_desc[:200], emotion_tag, context[:500], importance, now)
        )
    await db.commit()
    logger.info(f"[共同回忆] 保存: user={user_id[:6]} type={event_type} desc={event_desc[:30]}")


async def get_shared_memories(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """获取用户的共同回忆列表。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, event_type, event_desc, emotion_tag, context, importance,
                  recall_count, created_at, last_recalled
           FROM shared_memories WHERE user_id = ?
           ORDER BY importance DESC, created_at DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_recall_candidates(user_id: str, current_msg: str, limit: int = 3) -> List[Dict[str, Any]]:
    """根据当前消息召回相关共同回忆。

    匹配策略：关键词重叠 + 重要性加权随机。
    """
    db = await get_db()
    now = datetime.now().timestamp()

    # 获取所有回忆
    async with db.execute(
        """SELECT id, event_type, event_desc, emotion_tag, importance, recall_count, created_at
           FROM shared_memories WHERE user_id = ? AND importance >= 0.2
           ORDER BY importance DESC LIMIT 30""",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return []

    candidates = []
    msg_keywords = set(re.findall(r'[一-鿿]{2,6}', current_msg))

    for row in rows:
        desc = row["event_desc"]
        # 关键词匹配
        desc_keywords = set(re.findall(r'[一-鿿]{2,6}', desc))
        overlap = len(msg_keywords & desc_keywords)

        # 时间衰减：越久远的回忆召回概率越低，但重要回忆衰减更慢
        days_ago = (now - row["created_at"]) / 86400 if row["created_at"] else 999
        importance = row["importance"]
        effective_decay_days = 30 * (1 + importance)  # 重要回忆衰减窗口更长
        time_factor = max(0.1, 1.0 - days_ago / effective_decay_days)

        # 综合得分
        score = (overlap * 0.4 + importance * 0.4 + time_factor * 0.2)

        # 最近被回忆过的降低概率（避免重复）
        if row["last_recalled"]:
            hours_since_recall = (now - row["last_recalled"]) / 3600
            if hours_since_recall < 24:
                score *= 0.3

        if score > 0.1:
            candidates.append({**dict(row), "_score": score})

    if not candidates:
        # 无关键词匹配时，10% 概率随机召回一条重要回忆
        if random.random() < 0.1 and rows:
            top = max(rows, key=lambda r: r["importance"])
            return [dict(top)]
        return []

    # 按得分加权随机选择
    weights = [max(0.05, c["_score"]) for c in candidates]
    total = sum(weights)
    probs = [w / total for w in weights]
    selected_idx = random.choices(range(len(candidates)), weights=probs, k=min(limit, len(candidates)))

    result = [candidates[i] for i in selected_idx]

    # 更新 recall_count 和 last_recalled
    for item in result:
        await db.execute(
            """UPDATE shared_memories SET recall_count = recall_count + 1, last_recalled = ?
               WHERE id = ?""",
            (now, item["id"])
        )
    await db.commit()

    return result


async def boost_shared_memory(memory_id: int, boost: float = 0.1):
    """被成功回忆时提升重要性。"""
    db = await get_db()
    await db.execute(
        "UPDATE shared_memories SET importance = MIN(1.0, importance + ?) WHERE id = ?",
        (boost, memory_id)
    )
    await db.commit()


async def decay_shared_memories(base_rate: float = 0.01):
    """对共同回忆做重要性加权衰减。

    重要回忆衰减更慢：effective_rate = base_rate / (1 + importance)
    被回忆过的回忆衰减减半。
    """
    db = await get_db()
    now = datetime.now().timestamp()
    # 只衰减 7 天前创建的回忆
    cutoff = now - 7 * 86400
    await db.execute(
        """UPDATE shared_memories
           SET importance = MAX(0.1, importance - ? / (1 + importance) * CASE WHEN recall_count > 0 THEN 0.5 ELSE 1.0 END)
           WHERE created_at < ? AND importance > 0.1""",
        (base_rate, cutoff)
    )
    await db.commit()


# ============================================================
# 私人梗 (private_memes)
# ============================================================

async def save_private_meme(
    user_id: str,
    meme_type: str,
    content: str,
    origin_context: str = "",
    trigger_keywords: str = "",
    frequency: float = 0.3,
):
    """保存一条私人梗。

    meme_type: nickname / joke / catchphrase / code_word
    """
    db = await get_db()
    now = datetime.now().timestamp()
    # 去重
    async with db.execute(
        "SELECT id FROM private_memes WHERE user_id = ? AND meme_type = ? AND content = ?",
        (str(user_id), meme_type, content[:100])
    ) as cursor:
        existing = await cursor.fetchone()
    if existing:
        # 已存在，更新频率
        await db.execute(
            "UPDATE private_memes SET frequency = MIN(0.8, frequency + 0.05), last_used = ? WHERE id = ?",
            (now, existing["id"])
        )
    else:
        await db.execute(
            """INSERT INTO private_memes
               (user_id, meme_type, content, origin_context, trigger_keywords, frequency, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(user_id), meme_type, content[:100], origin_context[:300],
             trigger_keywords[:200], frequency, now)
        )
    await db.commit()
    logger.info(f"[私人梗] 保存: user={user_id[:6]} type={meme_type} content={content[:20]}")


async def get_private_memes(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """获取用户的私人梗列表。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, meme_type, content, origin_context, trigger_keywords,
                  frequency, usage_count, created_at, last_used
           FROM private_memes WHERE user_id = ?
           ORDER BY usage_count DESC, frequency DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def find_matching_meme(user_id: str, current_msg: str) -> Optional[Dict[str, Any]]:
    """根据当前消息匹配一个可用的私人梗。

    匹配策略：trigger_keywords 关键词命中 + 频率概率触发。
    """
    db = await get_db()
    now = datetime.now().timestamp()

    async with db.execute(
        """SELECT id, meme_type, content, trigger_keywords, frequency, usage_count, last_used
           FROM private_memes WHERE user_id = ?
           ORDER BY frequency DESC""",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return None

    for row in rows:
        keywords = row["trigger_keywords"] or ""
        if not keywords:
            continue
        # 检查关键词匹配
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
        matched = any(kw in current_msg for kw in keyword_list)
        if not matched:
            continue

        # 冷却检查：最近 1 小时内用过的梗不重复
        if row["last_used"]:
            hours_since = (now - row["last_used"]) / 3600
            if hours_since < 1:
                continue

        # 频率概率触发
        if random.random() < row["frequency"]:
            # 更新使用记录
            await db.execute(
                "UPDATE private_memes SET usage_count = usage_count + 1, last_used = ? WHERE id = ?",
                (now, row["id"])
            )
            await db.commit()
            return dict(row)

    return None


# ============================================================
# 重要日期 (important_dates)
# ============================================================

async def save_important_date(
    user_id: str,
    date_type: str,
    date_value: str,
    description: str = "",
    repeat_yearly: bool = True,
):
    """保存一个重要日期。

    date_type: birthday / anniversary / first_chat / special_day
    date_value: "MM-DD" 格式（年重复）或 "YYYY-MM-DD" 格式（一次性）
    """
    db = await get_db()
    now = datetime.now().timestamp()
    try:
        await db.execute(
            """INSERT INTO important_dates
               (user_id, date_type, date_value, description, repeat_yearly, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, date_type, date_value)
               DO UPDATE SET description = ?, repeat_yearly = ?""",
            (str(user_id), date_type, date_value, description[:200],
             1 if repeat_yearly else 0, now,
             description[:200], 1 if repeat_yearly else 0)
        )
        await db.commit()
        logger.info(f"[重要日期] 保存: user={user_id[:6]} type={date_type} date={date_value}")
    except Exception as e:
        logger.info(f"[重要日期] 保存失败: {e}")


async def get_important_dates(user_id: str) -> List[Dict[str, Any]]:
    """获取用户的所有重要日期。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, date_type, date_value, description, repeat_yearly, created_at
           FROM important_dates WHERE user_id = ?
           ORDER BY date_value""",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_today_dates(user_id: str, today_mm_dd: str) -> List[Dict[str, Any]]:
    """检查今天是否有匹配的重要日期（含年重复）。

    today_mm_dd: "MM-DD" 格式
    """
    db = await get_db()
    results = []

    # 匹配年重复日期（MM-DD 格式）
    async with db.execute(
        """SELECT id, date_type, date_value, description, repeat_yearly
           FROM important_dates
           WHERE user_id = ? AND repeat_yearly = 1
           AND (date_value = ? OR substr(date_value, 6) = ?)""",
        (str(user_id), today_mm_dd, today_mm_dd)
    ) as cursor:
        rows = await cursor.fetchall()
        results.extend([dict(r) for r in rows])

    # 匹配精确日期（YYYY-MM-DD 格式）
    today_full = datetime.now().strftime("%Y-%m-%d")
    async with db.execute(
        """SELECT id, date_type, date_value, description, repeat_yearly
           FROM important_dates
           WHERE user_id = ? AND date_value = ?""",
        (str(user_id), today_full)
    ) as cursor:
        rows = await cursor.fetchall()
        for r in rows:
            d = dict(r)
            if d not in results:
                results.append(d)

    return results


async def get_upcoming_dates(user_id: str, within_days: int = 7) -> List[Dict[str, Any]]:
    """获取即将到来的重要日期。"""
    db = await get_db()
    now = datetime.now()
    today_mm = now.strftime("%m-%d")

    results = []
    async with db.execute(
        """SELECT id, date_type, date_value, description, repeat_yearly
           FROM important_dates WHERE user_id = ? AND repeat_yearly = 1""",
        (str(user_id),)
    ) as cursor:
        rows = await cursor.fetchall()

    for row in rows:
        d = dict(row)
        date_str = d["date_value"]
        # 提取 MM-DD 部分
        if len(date_str) >= 5:
            mm_dd = date_str[-5:] if len(date_str) > 5 else date_str
            try:
                month, day = int(mm_dd[:2]), int(mm_dd[3:5])
                target = now.replace(month=month, day=day)
                if target < now:
                    target = target.replace(year=now.year + 1)
                days_until = (target - now).days
                if 0 < days_until <= within_days:
                    d["days_until"] = days_until
                    results.append(d)
            except (ValueError, IndexError):
                continue

    return sorted(results, key=lambda x: x.get("days_until", 999))
