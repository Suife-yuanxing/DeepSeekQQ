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

async def maybe_learn_catchphrase(user_id: str, _affection_score: float = None) -> Optional[str]:
    """从对方高频词学习新的口头禅。

    条件：好感度 >= CATCHPHRASE_LEARN_AFFECTION_MIN（默认300），每天最多学1个。

    真人化 P3-4.5：好感度统一通过 get_affection(user_id) 获取，
    _affection_score 参数仅保留用于向后兼容（已弃用）。
    """
    from .config import CATCHPHRASE_LEARN_AFFECTION_MIN
    from .db_affection import get_affection as _get_aff

    # 真人化4.5：统一从 get_affection() 获取好感度
    if _affection_score is not None:
        affection_score = _affection_score
    else:
        try:
            aff_data = await _get_aff(user_id)
            affection_score = aff_data.get("score", 0)
        except Exception:
            affection_score = 0

    if affection_score < CATCHPHRASE_LEARN_AFFECTION_MIN:
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
# 事件驱动人设演化 — 真人化 P3-4.3
# ============================================================

async def _get_topic_daily_counts(user_id: str, days: int = 7) -> Dict[str, Dict[str, int]]:
    """获取最近 N 天每个话题的每日提及次数。

    Returns:
        {topic: {"2026-06-19": 3, "2026-06-18": 1, ...}}
    """
    try:
        from .database import get_db
        from .personality import _topic_prefs_cache, _ensure_initialized

        _ensure_initialized()
        topics = [item["content"] for item in _topic_prefs_cache]
        if not topics:
            return {}

        db = await get_db()
        cutoff = time.time() - days * 86400
        async with db.execute(
            """SELECT content, timestamp FROM memories
               WHERE session_id LIKE ? AND role='assistant' AND timestamp > ?
               ORDER BY timestamp DESC""",
            (f"%{user_id}%", cutoff)
        ) as cursor:
            rows = await cursor.fetchall()

        from collections import defaultdict
        result = defaultdict(lambda: defaultdict(int))

        for row in rows:
            content = row["content"] or ""
            ts = row["timestamp"]
            day_key = time.strftime("%Y-%m-%d", time.localtime(ts))
            for topic in topics:
                if topic in content:
                    result[topic][day_key] += 1

        return {k: dict(v) for k, v in result.items()}
    except Exception:
        return {}


async def detect_sudden_obsession(user_id: str) -> Optional[str]:
    """检测「突然沉迷」事件（真人化 P3-4.3）。

    单日 ≥5 次聊某话题 → 触发"突然沉迷"事件。
    """
    try:
        counts = await _get_topic_daily_counts(user_id, days=3)
        today_key = time.strftime("%Y-%m-%d")

        for topic, daily in counts.items():
            today_count = daily.get(today_key, 0)
            if today_count < 5:
                continue

            # 检查前几天是否也高（如果是持续高则不算"突然"）
            prev_days = [c for d, c in daily.items() if d != today_key]
            prev_avg = sum(prev_days) / len(prev_days) if prev_days else 0

            if today_count >= prev_avg * 3:  # 今天的量是之前的3倍以上 → 突然沉迷
                hint = f"今天聊{topic}特别多（{today_count}次），你突然对{topic}产生了强烈的兴趣。聊天中会自然地提到。"
                logger.info(f"[人设演化] 突然沉迷: user={user_id[:8]} topic={topic} count={today_count}")
                return hint

        return None
    except Exception as e:
        logger.debug(f"[人设演化] 突然沉迷检测失败: {e}")
        return None


async def detect_interest_decline(user_id: str) -> Optional[str]:
    """检测「兴趣消退」事件（真人化 P3-4.3）。

    连续 ≥3 天某之前活跃的话题零提及 → 触发"兴趣消退"事件。
    """
    try:
        counts = await _get_topic_daily_counts(user_id, days=7)

        for topic, daily in counts.items():
            # 检查过去7天是否有连续3天零提及
            days_list = []
            for i in range(7):
                day_key = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
                days_list.append(daily.get(day_key, 0))

            # 找到连续零的序列
            zero_streak = 0
            for c in days_list:
                if c == 0:
                    zero_streak += 1
                else:
                    break  # 只检查从今天往前的连续零

            if zero_streak >= 3:
                # 检查之前是否活跃过（至少前4-7天有提及）
                older_mentions = sum(days_list[3:7])
                if older_mentions >= 2:
                    hint = (
                        f"你之前挺喜欢{topic}的，但最近{zero_streak}天完全没聊过了。"
                        f"兴趣在悄悄消退。偶尔提到时语气不如以前兴奋。"
                    )
                    logger.info(f"[人设演化] 兴趣消退: user={user_id[:8]} topic={topic} streak={zero_streak}d")
                    return hint

        return None
    except Exception as e:
        logger.debug(f"[人设演化] 兴趣消退检测失败: {e}")
        return None


async def get_event_drift_hints(user_id: str) -> List[str]:
    """事件驱动人设演化：检测突然沉迷 + 兴趣消退事件。

    替代单纯的时间窗口统计，改为事件触发——更像真人的兴趣变化模式。
    同时保留原有的频率统计作为补充。
    """
    hints = []

    # 原有频率统计（作为基础）
    freq_hints = await get_personality_drift_hints(user_id)
    if freq_hints:
        hints.extend(freq_hints)

    # 事件驱动检测
    obsession = await detect_sudden_obsession(user_id)
    if obsession and obsession not in hints:
        hints.append(obsession)

    decline = await detect_interest_decline(user_id)
    if decline and decline not in hints:
        hints.append(decline)

    return hints


# ============================================================
# 每周兴趣趋势评估（LLM）
# ============================================================

async def weekly_interest_evaluation(user_id: str):
    """每周一次用 LLM 评估兴趣变化趋势，写入 user_preferences。

    由 PERSONALITY_WEEKLY_EVAL_ENABLED 控制开关（默认关闭——目前无人读取结果）。
    """
    from .config import PERSONALITY_WEEKLY_EVAL_ENABLED
    if not PERSONALITY_WEEKLY_EVAL_ENABLED:
        logger.debug("[人设演化] 周评估已关闭 (PERSONALITY_WEEKLY_EVAL_ENABLED=false)")
        return

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
    """对所有活跃用户运行周评估（真人化Q5：仅评估好感度≥100的私聊用户）。"""
    try:
        from .database import get_db
        db = await get_db()
        cutoff = time.time() - 604800  # 7天内活跃
        async with db.execute(
            """SELECT DISTINCT m.user_id FROM memories m
               WHERE m.timestamp > ? AND m.role='user'
               AND m.user_id IN (SELECT a.user_id FROM affection a WHERE a.score >= 100)
               LIMIT 20""",
            (cutoff,)
        ) as cursor:
            rows = await cursor.fetchall()

        for row in rows:
            uid = row["user_id"]
            await weekly_interest_evaluation(uid)
    except Exception as e:
        logger.error(f"[人设演化] 批量周评估失败: {e}")


# ============================================================
# 口头禅双向影响 — 真人化 P3-4.4
# ============================================================

async def sync_catchphrase_influence(user_id: str, bot_reply: str = "") -> Optional[str]:
    """检测并记录口头禅的双向影响。

    Bot 的口头禅会逐渐影响用户的说话风格：
    1. 记录 bot 当前使用的口头禅
    2. 检查用户最近消息是否也用了 bot 的口头禅
    3. 如果用户被"传染"了，生成提示注入 prompt

    形成"互相影响"闭环：bot 学用户 → 用户学 bot → 用户画像反映 bot 影响。

    Returns:
        提示文本或 None（未检测到双向影响）
    """
    try:
        from .database import get_db
        from .personality import _catchphrases_cache as _cp_cache
        from .personality import _ensure_initialized as _ensure_cp_init

        _ensure_cp_init()
        if not _cp_cache:
            return None

        # 获取 bot 当前的口头禅列表
        bot_phrases = [cp["content"] for cp in _cp_cache[:8]]

        # 查询用户最近3天的消息，检查是否使用了 bot 的口头禅
        db = await get_db()
        cutoff = time.time() - 259200  # 3天
        async with db.execute(
            """SELECT content FROM memories
               WHERE session_id LIKE ? AND role='user' AND timestamp > ?
               ORDER BY timestamp DESC LIMIT 50""",
            (f"%{user_id}%", cutoff)
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows or len(rows) < 10:
            return None

        # 统计用户使用 bot 口头禅的频率
        user_text = " ".join(r["content"] for r in rows if r["content"])
        adopted = []
        for phrase in bot_phrases:
            if len(phrase) >= 2 and phrase in user_text:
                count = user_text.count(phrase)
                if count >= 2:  # 至少出现2次才算"被传染"
                    adopted.append((phrase, count))

        if not adopted:
            return None

        # 按使用频率排序，取前3个
        adopted.sort(key=lambda x: x[1], reverse=True)
        top_adopted = adopted[:3]

        # 记录到 user_preferences
        try:
            from .db_preferences import update_user_preference
            import json as _json
            await update_user_preference(
                user_id, "catchphrase_influence",
                "adopted_phrases",
                _json.dumps([p for p, c in top_adopted], ensure_ascii=False)
            )
        except Exception:
            pass

        # 生成提示
        phrase_list = "、".join(f"「{p}」" for p, c in top_adopted)
        hint = (
            f"【双向影响】他最近也开始说{phrase_list}了——"
            f"你的口头禅在影响他，你们的说话风格在互相靠近。"
            f"可以在聊天中自然地注意到这一点（但不要直接说出来）。"
        )
        logger.info(f"[人设演化] 口头禅双向影响: user={user_id[:8]} phrases={phrase_list}")
        return hint

    except Exception as e:
        logger.debug(f"[人设演化] 口头禅影响检测失败（非关键）: {e}")
        return None


async def get_catchphrase_influence_hint(user_id: str) -> Optional[str]:
    """获取缓存的「口头禅双向影响」提示（供 prompt 构建使用）。

    从 user_preferences 读取已记录的影响数据，无需重新扫描消息。
    """
    try:
        from .db_preferences import get_user_preferences
        import json as _json
        prefs = await get_user_preferences(user_id)
        influence_data = prefs.get("catchphrase_influence", {})
        adopted_raw = influence_data.get("adopted_phrases", "")
        if not adopted_raw:
            return None
        adopted = _json.loads(adopted_raw) if isinstance(adopted_raw, str) else adopted_raw
        if not adopted:
            return None
        phrase_list = "、".join(f"「{p}」" for p in adopted[:3])
        return (
            f"【双向影响】他最近也开始说{phrase_list}了——"
            f"你的口头禅在影响他，你们的说话风格在互相靠近。"
            f"可以在聊天中自然地注意到这一点（但不要直接说出来）。"
        )
    except Exception:
        return None
