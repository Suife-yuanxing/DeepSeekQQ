"""人设演化 — bot的兴趣随时间自然变化。

追踪最近7天话题频率：
- 连续高频 → 沉迷
- 完全不提 → 兴趣下降
- 口头禅可从对方高频词迁移（好感度600+）
- 每周一次 LLM 评估兴趣变化趋势
"""
import asyncio
import random
import re
import time
from collections import Counter
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


# ============================================================
# 话题频率追踪
# ============================================================

async def get_recent_topic_frequency(user_id: str, days: int = 7) -> Counter:
    """从 memories 表统计最近N天 bot 回复的话题关键词频率。"""
    try:
        from .database import get_db
        db = await get_db()
        cutoff = time.time() - days * 86400

        async with db.execute(
            "SELECT content FROM memories WHERE session_id LIKE ? AND role='assistant' AND timestamp > ?",
            (f"%{user_id}%", cutoff)
        ) as cursor:
            rows = await cursor.fetchall()

        # 导入话题列表
        from .personality import _topic_prefs_cache
        topics = [item["content"] for item in _topic_prefs_cache]

        counter = Counter()
        for row in rows:
            content = row["content"] or ""
            for topic in topics:
                if topic in content:
                    counter[topic] += 1
        return counter
    except Exception as e:
        logger.error(f"[人设演化] 话题频率查询失败: {e}")
        return Counter()


# ============================================================
# 兴趣漂移检测
# ============================================================

async def get_personality_drift_hints(user_id: str) -> List[str]:
    """生成兴趣漂移提示词。"""
    try:
        freq = await get_recent_topic_frequency(user_id, days=7)
    except Exception:
        return []

    from .personality import _topic_prefs_cache

    hints = []

    for item in _topic_prefs_cache:
        topic = item["content"]
        count = freq.get(topic, 0)

        if count >= 10:
            hints.append(f"最近沉迷{topic}，聊天中会自然地提到")
        elif count >= 5:
            hints.append(f"最近对{topic}挺感兴趣的")
        elif count == 0:
            if item.get("type") == "topic_love" and random.random() < 0.3:
                hints.append(f"之前喜欢{topic}但最近不怎么聊了")

    return hints


# ============================================================
# 口头禅迁移（好感度600+）
# ============================================================

async def maybe_learn_catchphrase(user_id: str, affection_score: float) -> Optional[str]:
    """从对方高频词学习新的口头禅。

    条件：好感度 >= 600，每天最多学1个。
    """
    if affection_score < 600:
        return None

    try:
        from .database import get_db
        db = await get_db()

        # 检查今天是否已经学过
        today_key = time.strftime("%Y%m%d")
        async with db.execute(
            "SELECT pref_value FROM user_preferences WHERE user_id=? AND pref_type='catchphrase_learned' AND pref_key=?",
            (user_id, today_key)
        ) as cursor:
            if await cursor.fetchone():
                return None  # 今天已学过了

        # 获取对方最近3天的高频词
        cutoff = time.time() - 259200
        async with db.execute(
            "SELECT content FROM memories WHERE session_id LIKE ? AND role='user' AND timestamp > ?",
            (f"%{user_id}%", cutoff)
        ) as cursor:
            rows = await cursor.fetchall()

        # 提取2-4字的中文短语，按频率排序
        all_text = " ".join(r["content"] for r in rows if r["content"])
        if len(all_text) < 20:
            return None

        phrases = re.findall(r'[一-鿿]{2,4}', all_text)
        freq = Counter(phrases)

        # 排除常见词和已有的口头禅
        from .personality import _catchphrases_cache
        existing = {c["content"] for c in _catchphrases_cache}
        stop_words = {"可以", "这个", "那个", "什么", "怎么", "一个", "不是", "没有", "知道", "觉得", "因为", "所以", "但是"}

        candidates = [
            (phrase, count) for phrase, count in freq.most_common(50)
            if phrase not in existing and phrase not in stop_words and count >= 3
        ]

        if not candidates:
            return None

        # 选择最高频的一个
        new_phrase, count = candidates[0]

        # 记录到 user_preferences
        from .db_preferences import update_user_preference
        await update_user_preference(
            user_id, "catchphrase_learned",
            today_key,
            new_phrase
        )

        # 添加到口头禅缓存
        _catchphrases_cache.append({
            "content": new_phrase,
            "frequency": 0.03,
            "context": "学来的口头禅",
        })

        logger.info(f"[人设演化] 学到新口头禅: {new_phrase} (来自用户 {user_id[:8]})")
        return new_phrase
    except Exception as e:
        logger.error(f"[人设演化] 口头禅学习失败: {e}")
        return None


# ============================================================
# 每周兴趣趋势评估（LLM）
# ============================================================

async def weekly_interest_evaluation(user_id: str):
    """每周一次用 LLM 评估兴趣变化趋势，写入 user_preferences。"""
    try:
        from .database import get_db
        db = await get_db()

        # 检查是否本周已评估过
        week_key = time.strftime("%Y-W%U")
        async with db.execute(
            "SELECT pref_value FROM user_preferences WHERE user_id=? AND pref_type='weekly_eval' AND pref_key=?",
            (user_id, week_key)
        ) as cursor:
            if await cursor.fetchone():
                return  # 本周已评估

        # 获取最近7天对话摘要
        cutoff = time.time() - 604800
        async with db.execute(
            "SELECT content FROM memories WHERE session_id LIKE ? AND role='assistant' AND timestamp > ? ORDER BY timestamp DESC LIMIT 50",
            (f"%{user_id}%", cutoff)
        ) as cursor:
            rows = await cursor.fetchall()

        if len(rows) < 10:
            return  # 数据不够

        recent_msgs = "\n".join([r["content"][:100] for r in rows[:30]])

        # LLM 评估
        prompt = f"""分析以下 bot 最近7天的回复，判断她的兴趣变化：

{recent_msgs}

请严格按JSON格式返回，不要有其他文字：
```json
{{
  "rising_interests": ["上升的兴趣1", "上升的兴趣2"],
  "declining_interests": ["下降的兴趣1"],
  "new_interests": ["新萌芽的兴趣"],
  "summary": "一句话总结兴趣变化趋势"
}}
```"""

        from .api import call_deepseek_api
        raw = await call_deepseek_api(
            [{"role": "user", "content": prompt}],
            temperature=0.3,
            task_type="analysis"
        )
        import json as _json
        match = re.search(r'\{[\s\S]*\}', raw)
        if match:
            data = _json.loads(match.group())
            from .db_preferences import update_user_preference
            await update_user_preference(user_id, "weekly_eval", week_key, _json.dumps(data, ensure_ascii=False))
            logger.info(f"[人设演化] 周评估完成: {user_id[:8]} → {data.get('summary', '')[:50]}")
    except Exception as e:
        logger.debug(f"[人设演化] 周评估失败（不影响主流程）: {e}")


async def weekly_eval_all_users():
    """对所有活跃用户运行周评估。"""
    try:
        from .database import get_db
        db = await get_db()
        cutoff = time.time() - 604800  # 7天内活跃
        async with db.execute(
            "SELECT DISTINCT user_id FROM memories WHERE timestamp > ? AND role='user' LIMIT 20",
            (cutoff,)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            uid = row["user_id"]
            await weekly_interest_evaluation(uid)
    except Exception as e:
        logger.error(f"[人设演化] 批量周评估失败: {e}")
