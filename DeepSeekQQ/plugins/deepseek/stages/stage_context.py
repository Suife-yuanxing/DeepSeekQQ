"""Stage: 上下文分析 — 核心分析流程，包含情绪/搜索/天气/记忆/行为等子分析。

这是最复杂的 stage，内部有 7 个 _run_* 子函数组成完整的分析管道。
"""
import asyncio
import random as _random
from typing import Optional

from nonebot import logger

from ..context_analyzer import AnalysisResult
from ..context_analyzer import analyze_context_and_emotion
from ..context_analyzer import update_bot_emotion
from ..handler_helpers import classify_message_complexity
from ..handler_helpers import get_emotion_params
from ..handler_helpers import build_reply_gap_hint
from ..memory import get_date_hint
from ..memory import get_private_meme_hint
from ..memory import get_shared_memory_hint
from ..memory import get_user_pref_hints
from ..memory import save_and_get_context_with_history
from ..pipeline import ChatContext
from ..pipeline import stage
from ..search import extract_search_query
from ..search import format_search_for_prompt
from ..search import search
from ..search import should_search
from ..share_parser import get_recent_shares
from ..utils import safe_task
from ..world_context import build_world_context_prompt
from ..world_context import extract_city_from_message


# ============================================================
# 内部分析函数
# ============================================================

async def _run_core_analysis(ctx: ChatContext, history_for_analysis: list):
    """第一批并行：分析 + 搜索 + 天气 + 提醒。"""
    async def _do_analysis():
        current_shares = get_recent_shares(ctx.session_id) if ctx.has_share else None
        return await analyze_context_and_emotion(ctx.raw_msg, history_for_analysis, ctx.user_id, current_shares)

    async def _do_search():
        search_decision = should_search(ctx.raw_msg)
        if search_decision.get("need_search"):
            ctx.is_explicit_search = search_decision.get("is_explicit", False)
            query = extract_search_query(ctx.raw_msg)
            result = await search(query)
            if result:
                logger.info(f"[搜索] 找到 {len(result.results)} 条结果 | 显式={ctx.is_explicit_search}")
            return result
        return None

    async def _do_weather():
        user_city = extract_city_from_message(ctx.raw_msg)
        if not user_city:
            for tag in ctx.relevant_tags:
                tag_str = str(tag)
                for city_name in ["上海", "北京", "广州", "深圳", "杭州", "成都", "武汉", "南京", "重庆", "西安", "苏州", "天津"]:
                    if city_name in tag_str:
                        user_city = city_name
                        break
                if user_city:
                    break
        # 先获取结构化天气数据（供行为引擎使用）
        from ..world_context import get_weather
        weather_info = await get_weather(user_city)
        if weather_info:
            weather_info._city = user_city
            ctx._weather_info = weather_info
        return await build_world_context_prompt(user_city)

    async def _do_reminders():
        from ..reminder import get_pending_reminders_context
        return await get_pending_reminders_context(ctx.user_id)

    results = await asyncio.gather(
        _do_analysis(), _do_search(), _do_weather(), _do_reminders(),
        return_exceptions=True
    )
    analysis, search_result, world_ctx, reminder_ctx = results

    # 处理并行任务中的异常：失败的任务用默认值，不中断 pipeline
    if isinstance(analysis, Exception):
        logger.error(f"[分析] 情绪/上下文分析失败: {analysis}")
        from ..context_analyzer import ContextAnalysis
        from ..context_analyzer import EmotionState
        analysis = AnalysisResult(context=ContextAnalysis(), emotion=EmotionState())
    if isinstance(search_result, Exception):
        logger.warning(f"[搜索] 搜索失败: {search_result}")
        search_result = None
    if isinstance(world_ctx, Exception):
        logger.warning(f"[天气] 天气查询失败: {world_ctx}")
        world_ctx = ""
    if isinstance(reminder_ctx, Exception):
        logger.warning(f"[提醒] 提醒查询失败: {reminder_ctx}")
        reminder_ctx = ""

    ctx.analysis = analysis
    ctx.search_result = search_result
    ctx.world_context = world_ctx
    ctx.reminder_context = reminder_ctx
    ctx.emotion_params = get_emotion_params(ctx.analysis.emotion)

    if ctx.analysis.context.referenced_entity:
        logger.info(f"[指代消解] 检测到指代: {ctx.analysis.context.referenced_entity}")


async def _run_batch1_queries(ctx: ChatContext):
    """第二批并行：无依赖的 DB/状态查询。"""
    from ..database import check_and_trigger_milestone
    from ..database import has_user_message_today
    from ..emotion_deep import get_emotion_memory_hint

    async def _get_affection_decay():
        if ctx.affection and ctx.session_recovery:
            from ..db_affection import get_affection_decay_hint
            return await get_affection_decay_hint(ctx.user_id)
        return None

    async def _get_undisclosed():
        if ctx.affection and _random.random() < 0.15:
            from ..db_session import get_undisclosed_facts
            return await get_undisclosed_facts(ctx.user_id, ctx.affection.get("score", 0))
        return None

    batch1_results = await asyncio.gather(
        update_bot_emotion(ctx.raw_msg, ctx.analysis.emotion, ctx.user_id),  # [0]
        get_emotion_memory_hint(ctx.user_id, ctx.raw_msg),              # [1]
        get_user_pref_hints(ctx.user_id),                               # [2]
        has_user_message_today(ctx.session_id),                         # [3]
        get_shared_memory_hint(ctx.user_id, ctx.raw_msg),               # [4]
        get_private_meme_hint(ctx.user_id, ctx.raw_msg),                # [5]
        get_date_hint(ctx.user_id),                                     # [6]
        check_and_trigger_milestone(ctx.user_id),                       # [7]
        _get_affection_decay(),                                         # [8]
        _get_undisclosed(),                                             # [9]
        return_exceptions=True
    )

    # 解包 batch1 结果，异常项用默认值
    def _safe(val, default=None):
        return default if isinstance(val, Exception) else val

    ctx.bot_mood_result = _safe(batch1_results[0], {"dominant": "平静", "reason": ""})
    ctx.emotion_memory_hint = _safe(batch1_results[1], "") or ""
    ctx.user_prefs = _safe(batch1_results[2], {})
    ctx.is_first_today = not _safe(batch1_results[3], False)
    ctx.shared_memory_hint = _safe(batch1_results[4], "") or ""
    ctx.private_meme_hint = _safe(batch1_results[5], "") or ""
    ctx.date_hint = _safe(batch1_results[6], "") or ""
    ctx.milestone_hint = _safe(batch1_results[7])
    ctx.affection_decay_hint = _safe(batch1_results[8])
    ctx.disclosure_hint = _safe(batch1_results[9])
    if ctx.disclosure_hint:
        from ..database import mark_disclosed
        safe_task(mark_disclosed(ctx.user_id, ctx.disclosure_hint["key"]))

    # 情绪系统深化
    if ctx.bot_mood_result.get("recovery_stage"):
        ctx.emotion_recovery_hint = ctx.bot_mood_result["recovery_stage"]
    if ctx.bot_mood_result.get("swing_hint"):
        ctx.emotion_recovery_hint = ctx.bot_mood_result["swing_hint"]
    if ctx.bot_mood_result.get("contagion"):
        ctx.contagion_result = ctx.bot_mood_result["contagion"]

    # 情绪因果链：最近情绪变化趋势
    from ..context_analyzer import get_emotion_cause_chain
    cause_chain = await get_emotion_cause_chain(ctx.user_id)
    if cause_chain:
        ctx.emotion_memory_hint = (ctx.emotion_memory_hint or "") + f"\n情绪变化趋势：{cause_chain}"
        ctx.bot_mood_result["valence"] = ctx.bot_mood_result.get("valence", 0) + ctx.contagion_result.get("valence_delta", 0)
        ctx.bot_mood_result["arousal"] = ctx.bot_mood_result.get("arousal", 0.2) + ctx.contagion_result.get("arousal_delta", 0)

    # B23: 情绪感染后重新计算 emotion_params，确保 temperature/max_tokens 反映最新情绪
    if ctx.contagion_result:
        ctx.emotion_params = get_emotion_params(ctx.analysis.emotion)


def _run_sync_computations(ctx: ChatContext) -> str:
    """同步计算：作息、对话节奏、行为模式。返回 bot_mood_dominant。"""
    from ..schedule import get_schedule_state
    ctx.schedule = get_schedule_state()

    # 当前活动状态（activity_sim）
    from ..activity_sim import get_activity_hint
    ctx.activity_hint = get_activity_hint()
    bot_mood_dominant = ctx.bot_mood_result.get("dominant", "平静") if ctx.bot_mood_result else "平静"

    # 对话节奏：话题桥接/过渡
    from ..dialogue_rhythm import get_icebreaker_context
    from ..dialogue_rhythm import get_topic_bridge
    from ..dialogue_rhythm import get_topic_transition_hint
    prev_topic = ""
    if ctx.session_recovery:
        prev_topic = ctx.session_recovery.get("last_topic", "")
    else:
        ctx.session_recovery = {}  # B28: 防止后续 None 调用
    if ctx.analysis.context.topic_shift_score > 0.5 and prev_topic:
        ctx.topic_bridge = get_topic_bridge(
            prev_topic, ctx.analysis.context.topic_summary,
            ctx.analysis.context.topic_shift_score
        )
        ctx.topic_transition = get_topic_transition_hint(
            prev_topic, ctx.analysis.context.topic_summary,
            ctx.analysis.context.topic_shift_score,
            ctx.analysis.context.user_intent,
        )

    # 场景路由（prompt_templates 集成）
    try:
        from ..prompt_templates import classify_scenes
        from ..handler_helpers import is_question
        from ..handler_helpers import is_greeting
        ctx.scenes = classify_scenes(
            user_msg=ctx.raw_msg,
            is_group=ctx.is_group,
            has_shares=ctx.has_share,
            is_question=is_question(ctx.raw_msg),
            is_greeting=is_greeting(ctx.raw_msg),
            is_emotional=ctx.analysis.emotion.confidence >= 0.4 and ctx.analysis.emotion.dominant != "平静",
            has_location=bool(ctx.world_context),
            is_simple=ctx.complexity == "simple",
        )
    except Exception:
        ctx.scenes = []

    # 行为模式（使用结构化天气数据，避免 regex 解析格式化字符串）
    from ..behavior_engine import get_behavior_hint
    weather_condition = ""
    weather_temp = ""
    user_city = ""
    if ctx._weather_info:
        weather_condition = ctx._weather_info.condition or ""
        weather_temp = ctx._weather_info.temp or ""
        user_city = getattr(ctx._weather_info, '_city', "") or ""
    schedule_period = ctx.schedule.period if ctx.schedule else "active"
    ctx.behavior_hint = get_behavior_hint(
        weather_condition, weather_temp, schedule_period, bot_mood_dominant, city=user_city,
        affection_score=ctx.affection.get("score", 0),
    ) or ""

    # === 群聊热度状态机 ===
    if ctx.is_group:
        try:
            from ..heat_engine import update_heat as _update_heat
            from ..heat_engine import get_group_heat_description as _get_heat_desc
            _update_heat(ctx.session_id, is_group=True)
            ctx.heat_state = _get_heat_desc(ctx.session_id)
        except Exception:
            pass

    # === 社交Feed注入决策 ===
    try:
        from ..social_feed import get_recent_feed
        from ..social_feed import get_scroll_memory_summary
        from ..heat_engine import should_interject
        from ..heat_engine import HeatState
        from ..heat_engine import get_heat_state as _get_heat_state

        feed_items = get_recent_feed(limit=5, max_age_minutes=240)
        has_fresh = len(feed_items) > 0

        # 决策：是否注入feed到prompt
        if ctx.is_group:
            heat_state = _get_heat_state(ctx.session_id, is_group=True)
            if heat_state in (HeatState.IDLE, HeatState.COLD, HeatState.WARM):
                ctx.should_inject_feed = has_fresh
        else:
            # 私聊：总是注入（LLM自己决定用不用）
            ctx.should_inject_feed = has_fresh

        if ctx.should_inject_feed and has_fresh:
            ctx.scroll_hint = get_scroll_memory_summary(limit=3) or ""
    except Exception:
        pass

    return bot_mood_dominant


async def _run_batch2_queries(ctx: ChatContext, bot_mood_dominant: str):
    """第三批并行：依赖 batch1 结果的查询（破冰/群聊社交/个性化）。"""
    from ..dialogue_rhythm import get_icebreaker_context

    async def _get_icebreaker():
        if ctx.is_first_today and ctx.session_recovery:
            return await get_icebreaker_context(ctx.session_recovery, ctx.bot_mood_result) or ""
        return ""

    async def _get_group_social():
        if ctx.is_group:
            group_id = ctx.session_id.replace("group_", "")
            from ..db_group import update_member_activity
            from ..group_atmosphere import get_group_social_context
            social_ctx = await get_group_social_context(group_id, ctx.raw_msg)
            safe_task(update_member_activity(group_id, ctx.user_id))
            return social_ctx
        return {}

    async def _get_personalization():
        from ..db_session import get_or_create_user_profile
        from ..personalization import get_personalization_hints
        profile = await get_or_create_user_profile(ctx.user_id)
        custom_nickname = profile.get("nickname", "") if profile else ""
        # 读取用户画像摘要（bot 对自己的了解）
        if profile:
            ctx.user_profile_summary = profile.get("bot_self_summary", "") or ""
        affection_score = ctx.affection.get("score", 0)
        relationship_style = ctx.user_prefs.get("relationship_style", "")
        total_chats = ctx.affection.get("total_chats", 0)
        streak_days = ctx.affection.get("streak_days", 0)
        first_interaction = ctx.affection.get("first_interaction", 0) if "first_interaction" in ctx.affection else 0
        return await get_personalization_hints(
            ctx.user_id, affection_score, relationship_style, custom_nickname,
            bot_mood_dominant, total_chats, streak_days, first_interaction,
        )

    batch2_results = await asyncio.gather(
        _get_icebreaker(),       # [0]
        _get_group_social(),     # [1]
        _get_personalization(),  # [2]
        return_exceptions=True
    )

    def _safe2(val, default=None):
        return default if isinstance(val, Exception) else val

    ctx.icebreaker_hint = _safe2(batch2_results[0], "")
    social_ctx = _safe2(batch2_results[1], {})
    if social_ctx:
        ctx.group_social_hint = social_ctx.get("social_hint", "")
        ctx.group_meme_hint = social_ctx.get("meme_hint", "")
        ctx.group_role_hint = social_ctx.get("role_hint", "")
    personal_hints = _safe2(batch2_results[2], {})
    ctx.nickname_hint = personal_hints.get("nickname_hint", "")
    ctx.interest_hint = personal_hints.get("interest_hint", "")
    ctx.growth_hint = personal_hints.get("growth_hint", "")
    ctx.catchphrase_hint = personal_hints.get("catchphrase_hint", "")


async def _run_fatigue_and_gap(ctx: ChatContext, bot_mood_dominant: str):
    """对话疲劳感知 + 已读不回感知。"""
    from ..conversation_fatigue import analyze_conversation_fatigue
    fatigue_result = analyze_conversation_fatigue(ctx.recent_memories, ctx.raw_msg, ctx.schedule)
    ctx.fatigue_level = fatigue_result["level"]
    ctx.fatigue_hint = fatigue_result["hint"]
    if ctx.fatigue_level >= 2:
        logger.info(f"[疲劳感知] level={ctx.fatigue_level} score={fatigue_result['score']} signals={fatigue_result['signals']}")

    # 已读不回感知
    import time as _time

    from ..db_memories import get_last_bot_reply_time
    last_bot_ts = await get_last_bot_reply_time(ctx.session_id)
    if last_bot_ts > 0:
        gap_seconds = _time.time() - last_bot_ts
        ctx.reply_gap_hint = build_reply_gap_hint(
            gap_seconds, ctx.affection, ctx.schedule, bot_mood_dominant
        )


async def _run_personality_drift(ctx: ChatContext):
    """人设演化：兴趣漂移检测 + 口头禅学习。"""
    try:
        from ..personality_drift import get_personality_drift_hints, maybe_learn_catchphrase

        # 兴趣漂移提示
        drift_hints = await get_personality_drift_hints(ctx.user_id)
        if drift_hints:
            ctx.personality_drift_hints = drift_hints

        # 尝试学习口头禅（好感度门槛由 config 控制，默认 300）
        aff_score = ctx.affection.get("score", 0)
        from ..config import CATCHPHRASE_LEARN_AFFECTION_MIN
        if aff_score >= CATCHPHRASE_LEARN_AFFECTION_MIN:
            from ..utils import safe_task
            safe_task(maybe_learn_catchphrase(ctx.user_id, aff_score))
    except Exception as e:
        logger.debug(f"[人设演化] 跳过（非关键路径）: {e}")


async def _run_value_analysis(ctx: ChatContext):
    """价值体系分析：检测话题立场冲突，查询历史立场。"""
    try:
        aff_score = ctx.affection.get("score", 0)

        # 获取价值提示（关键词匹配 + 冲突检测，不调用LLM）
        from ..values import get_value_hints as _get_value_hints
        ctx.value_hints = _get_value_hints(ctx.raw_msg, aff_score)

        # 查询该用户相关的历史立场
        from ..opinion_tracker import get_past_opinions, build_past_opinions_hint
        ctx.past_opinions = await get_past_opinions(ctx.user_id, limit=5)
        if ctx.past_opinions:
            ctx.past_opinions_hint = build_past_opinions_hint(ctx.past_opinions)

        if ctx.value_hints:
            logger.debug(f"[价值体系] 检测到 {len(ctx.value_hints)} 条立场提示")
    except Exception as e:
        logger.debug(f"[价值体系] 分析跳过（非关键）: {e}")


async def _run_full_analysis(ctx: ChatContext, history_for_analysis: list):
    """完整分析流程：拆分为核心分析 → 状态查询 → 同步计算 → 深化查询 → 疲劳感知。"""
    await _run_core_analysis(ctx, history_for_analysis)
    await _run_batch1_queries(ctx)
    bot_mood_dominant = _run_sync_computations(ctx)
    await _run_batch2_queries(ctx, bot_mood_dominant)
    await _run_fatigue_and_gap(ctx, bot_mood_dominant)
    await _run_personality_drift(ctx)
    await _run_value_analysis(ctx)


# ============================================================
# Stage 定义
# ============================================================

@stage("context_analysis")
async def _stage_context(ctx: ChatContext) -> Optional[str]:
    # 用户回复了，取消追问状态
    from ..follow_up import record_user_reply
    record_user_reply(ctx.session_id)

    ctx.recent_memories, ctx.relevant_tags, ctx.affection, ctx.mood, history_for_analysis = \
        await save_and_get_context_with_history(ctx.session_id, ctx.user_id, ctx.raw_msg)

    # 话题追踪：在 memories 加载后注入话题上下文（避免 session_recovery 阶段 memories 为空）
    from ..topic_tracker import get_topic_context
    topic_context = get_topic_context(ctx.session_id, ctx.recent_memories)
    if topic_context:
        if ctx.session_recovery is None:
            ctx.session_recovery = {}
        ctx.session_recovery["topic_context"] = topic_context
        logger.debug(f"[话题追踪] 注入话题上下文: {topic_context[:50]}...")

    # B18+B24: 延迟复杂度分类 — 此时语音/图片/share 已处理完毕，用更新后的 raw_msg 重新判断
    # B24: 语音消息即使 voice_features 为空，raw_msg 可能已被 STT 更新，需要重新分类
    _needs_reclassify = (
        ctx.has_share or bool(ctx.voice_features) or bool(ctx.image_path)
        or (ctx.complexity == "simple" and len(ctx.raw_msg) > 10)  # STT 可能已更新 raw_msg
    )
    if ctx.raw_msg and _needs_reclassify:
        _has_image = bool(ctx.image_path)
        _has_voice = bool(ctx.voice_features) or (ctx.complexity == "simple" and len(ctx.raw_msg) > 10)
        ctx.complexity = classify_message_complexity(ctx.raw_msg, _has_image, _has_voice)
        logger.debug(f"[复杂度] 延迟重分类: {ctx.complexity} (raw_msg_len={len(ctx.raw_msg)}, img={_has_image}, voice={_has_voice})")

    # === 简单消息：跳过深度分析，直接用默认值 ===
    if ctx.complexity == "simple":
        from ..context_analyzer import ContextAnalysis
        from ..context_analyzer import EmotionState
        from ..schedule import get_schedule_state
        ctx.analysis = AnalysisResult(context=ContextAnalysis(), emotion=EmotionState())
        ctx.search_result = None
        ctx.world_context = ""
        ctx.bot_mood_result = {"dominant": "平静", "reason": ""}
        ctx.emotion_params = get_emotion_params(None)
        ctx.schedule = get_schedule_state()

        # ★ 轻量行为注入：不跑全量分析但给一点生活感
        # 天气信息：simple 分支不跑 _do_weather，用 WEATHER_CITY 兜底获取
        try:
            from ..world_context import get_weather
            from ..config import WEATHER_CITY
            weather_info = await get_weather(None)  # None → 用 WEATHER_CITY 兜底
            if weather_info:
                ctx._weather_info = weather_info
                weather_cond = weather_info.condition or ""
                weather_temp = weather_info.temp or ""
            else:
                weather_cond = ""
                weather_temp = ""
        except Exception:
            weather_cond = ""
            weather_temp = ""

        schedule_period = ctx.schedule.period if ctx.schedule else "active"
        aff_score = ctx.affection.get("score", 0)

        from ..behavior_engine import get_lightweight_behavior_hint
        ctx.behavior_hint = get_lightweight_behavior_hint(
            weather_condition=weather_cond,
            weather_temp=weather_temp,
            schedule_period=schedule_period,
            affection_score=aff_score,
            city=WEATHER_CITY,
        ) or ""

        logger.info(f"[快速通道] 简单消息，跳过深度分析: {ctx.raw_msg[:20]}")
    else:
        await _run_full_analysis(ctx, history_for_analysis)
        # ★ 将 WEATHER_CITY 也存入 _weather_info，确保天气行为兜底
        if not getattr(ctx, '_weather_info', None):
            try:
                from ..world_context import get_weather
                from ..config import WEATHER_CITY
                weather_info = await get_weather(None)
                if weather_info:
                    ctx._weather_info = weather_info
            except Exception:
                pass

    return None
