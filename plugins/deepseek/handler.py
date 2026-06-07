"""主消息处理器 — Pipeline 架构。

借鉴 ECC 的 Hook 系统，将消息处理拆分为有序的 Pipeline 阶段。
每个阶段可短路（返回 SKIP 跳过后续），新增功能只需注册一个阶段。
"""
import os
import time
import asyncio
import random
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Any, Callable, Coroutine
from dataclasses import dataclass, field

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, Message, MessageSegment
from nonebot import logger

from .config import REPLY_LENGTH_CONFIG, RANDOM_REPLY_CHANCE, ANALYSIS_HISTORY_LIMIT, CHAT_HISTORY_MULTIPLIER, PHONE_CONTROL_ENABLED, MY_QQ, STT_ENABLED
from .prompt import _build_system_prompt, estimate_reply_length
from .utils import split_long_reply, calc_message_delay, get_session_id, check_rate_limit, filter_novel_actions
from .memory import save_and_get_context, save_reply, apply_affection_delta, save_and_get_context_with_history, get_user_pref_hints, recover_session_context
from .share_parser import extract_and_cache_shares, get_recent_shares
from .share_prompt import build_analysis_prompt
from .api import call_deepseek_api
from .voice import send_voice, should_send_voice
from .stt import recognize_voice
from .context_analyzer import analyze_context_and_emotion, AnalysisResult, update_bot_emotion
from .search import should_search, search, format_search_for_prompt, extract_search_query
from .reminder import is_reminder_request, create_reminder, list_reminders, cancel_reminder_by_id, get_pending_reminders_context, _generate_reminder_reply
from .world_context import build_world_context_prompt, extract_city_from_message
from .media import split_reply_and_links, extract_shareable_from_search, build_rich_message
from .sticker import parse_sticker_tag, select_sticker, should_send_sticker_fallback, filter_sticker_tag, select_sticker_with_search
from .security import scan_input, get_blocked_reply
from .plugin_manager import get_enabled_plugins, load_plugins_from_dir
from .image_gen import should_generate_image, generate_image, _extract_draw_prompt

# 拆分出的子模块
from .handler_helpers import (
    make_reply, make_quote_reply, is_bot_at, is_multi_topic,
    is_question, is_greeting, detect_greeting_type, get_morning_time_hint,
    get_night_affection_hint, has_time_gap, should_quote, parse_target_lines,
    is_night_farewell,
)
from .handler_humanize import introduce_typo, introduce_mind_change, introduce_uncertainty, maybe_add_kaomoji

# 向后兼容：现有测试引用的内部函数名
_parse_target_lines = parse_target_lines
_reply = make_reply
_quote_reply = make_quote_reply


# ============================================================
# 情绪驱动参数映射（功能⑤）
# ============================================================

def get_emotion_params(emotion) -> dict:
    """根据 EmotionState 的 VA 值返回行为参数。"""
    if emotion is None or emotion.confidence < 0.4:
        return {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"}

    v, a = emotion.valence, emotion.arousal

    if v > 0.5 and a > 0.7:    # 兴奋
        return {"max_tokens": 1800, "temperature": 1.1, "sticker_chance": 0.50, "target_lines": "4-5"}
    elif v > 0.3 and a > 0.5:  # 开心
        return {"max_tokens": 1500, "temperature": 1.0, "sticker_chance": 0.40, "target_lines": "3-4"}
    elif v < -0.5 and a > 0.5: # 生气
        return {"max_tokens": 600, "temperature": 0.6, "sticker_chance": 0.05, "target_lines": "1"}
    elif v < -0.3:             # 难过
        return {"max_tokens": 800, "temperature": 0.7, "sticker_chance": 0.10, "target_lines": "1-2"}
    elif a < 0.3:              # 平静
        return {"max_tokens": 1200, "temperature": 0.8, "sticker_chance": 0.20, "target_lines": "2-3"}
    elif v > 0 and a > 0.5:    # 害羞
        return {"max_tokens": 1000, "temperature": 0.9, "sticker_chance": 0.30, "target_lines": "2"}
    else:                      # 默认
        return {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"}


# ============================================================
# 消息分级（真人化：简单消息快速响应）
# ============================================================

def classify_message_complexity(raw_msg: str, has_image: bool, has_voice: bool) -> str:
    """判断消息复杂度：simple / normal / complex。

    simple: 短文本（"嗯"、"哈哈"、"好的"）→ 跳过深度分析，快速回复
    normal: 一般消息 → 完整 pipeline
    complex: 图片/长文/明确提问 → 完整 pipeline + 可能搜索
    """
    msg = raw_msg.strip()
    if len(msg) <= 5 and not has_image and not has_voice:
        return "simple"
    if has_image or len(msg) > 50 or any(kw in msg for kw in [
        "详细", "分析", "解释", "为什么", "怎么弄", "帮我", "教我",
        "介绍", "具体", "怎么说", "什么意思",
    ]):
        return "complex"
    return "normal"




# ============================================================
# Pipeline 数据结构
# ============================================================

_SKIP = object()


@dataclass
class ChatContext:
    """Pipeline 共享上下文，在各阶段间传递数据。"""
    bot: Bot
    event: MessageEvent
    raw_msg: str = ""
    session_id: str = ""
    user_id: str = ""
    is_group: bool = False
    has_share: bool = False
    analysis: Optional[Any] = None
    bot_mood_result: Optional[dict] = None
    recent_memories: list = field(default_factory=list)
    relevant_tags: list = field(default_factory=list)
    affection: dict = field(default_factory=dict)
    mood: dict = field(default_factory=dict)
    search_result: Optional[Any] = None
    is_explicit_search: bool = False
    reminder_context: str = ""
    world_context: str = ""
    reply_text: str = ""
    emotion_params: dict = field(default_factory=lambda: {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"})
    user_prefs: dict = field(default_factory=dict)
    image_path: str = ""
    session_recovery: Optional[dict] = None
    disclosure_hint: Optional[str] = None
    affection_decay_hint: Optional[str] = None
    milestone_hint: Optional[str] = None
    is_first_today: bool = False
    schedule: Any = None  # ScheduleState from schedule.py
    voice_features: dict = field(default_factory=dict)  # 语音情绪特征
    # 记忆系统深化
    shared_memory_hint: str = ""
    private_meme_hint: str = ""
    date_hint: str = ""
    # 对话节奏优化
    topic_bridge: str = ""
    icebreaker_hint: str = ""
    topic_transition: str = ""
    # 情绪系统深化
    emotion_recovery_hint: str = ""
    emotion_memory_hint: str = ""
    contagion_result: dict = field(default_factory=dict)
    # 社交能力增强
    group_social_hint: str = ""
    group_meme_hint: str = ""
    group_role_hint: str = ""
    # 行为模式丰富
    behavior_hint: str = ""
    # 个性化深化
    nickname_hint: str = ""
    interest_hint: str = ""
    growth_hint: str = ""
    catchphrase_hint: str = ""
    # 真人化优化
    complexity: str = "normal"        # simple / normal / complex


# ============================================================
# Pipeline 注册
# ============================================================

PipelineStage = Callable[[ChatContext], Coroutine[Any, Any, Optional[Any]]]
_PIPELINE: List[tuple[str, PipelineStage]] = []


def stage(name: str):
    def decorator(func: PipelineStage):
        _PIPELINE.append((name, func))
        return func
    return decorator


# ============================================================
# Pipeline 阶段定义
# ============================================================

@stage("private_whitelist")
async def _stage_private_whitelist(ctx: ChatContext) -> Optional[str]:
    if not ctx.is_group and ctx.user_id != MY_QQ:
        logger.debug(f"[私聊白名单] 忽略非主人私聊: user={ctx.user_id[:6]}")
        return _SKIP
    return None


@stage("security")
async def _stage_security(ctx: ChatContext) -> Optional[str]:
    if not ctx.raw_msg:
        return None
    is_safe, reason = scan_input(ctx.raw_msg, ctx.user_id)
    if not is_safe:
        reply = get_blocked_reply(reason)
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply)))
        logger.warning(f"[安全] 拦截消息: user={ctx.user_id[:6]} reason={reason}")
        return _SKIP
    return None


@stage("session_recovery")
async def _stage_session_recovery(ctx: ChatContext) -> Optional[str]:
    ctx.session_recovery = await recover_session_context(ctx.session_id, ctx.user_id)
    return None


@stage("voice_recognition")
async def _stage_voice(ctx: ChatContext) -> Optional[str]:
    if not STT_ENABLED:
        return None
    has_voice = any(seg.type == "record" for seg in ctx.event.get_message())
    if has_voice and not ctx.raw_msg:
        recognized = await recognize_voice(ctx.event)
        if recognized:
            ctx.raw_msg = recognized
            logger.info(f"[STT] 语音识别结果: {ctx.raw_msg[:50]}")

            # 语音情绪识别（P1）：异步提取音频特征
            from .stt import _extract_voice_url, _download_voice
            voice_url = _extract_voice_url(ctx.event)
            if voice_url:
                local_path = await _download_voice(voice_url)
                if local_path:
                    from .voice_emotion import extract_voice_features
                    features = await extract_voice_features(local_path)
                    if features:
                        ctx.voice_features = features
                        logger.info(f"[语音情绪] {features.get('estimated_emotion', '未知')} | 音量:{features.get('rms_volume', 0):.0f}")
        else:
            logger.info("[STT] 语音识别失败或无内容")
            try:
                await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("听不太清楚呢...能打字告诉我吗？")))
            except Exception:
                pass
            return _SKIP
    return None


@stage("rate_limit")
async def _stage_rate_limit(ctx: ChatContext) -> Optional[str]:
    if not check_rate_limit(ctx.user_id):
        logger.info(f"[限流] 用户 {ctx.user_id} 请求过快，已忽略")
        return _SKIP
    return None


@stage("share_extract")
async def _stage_share(ctx: ChatContext) -> Optional[str]:
    ctx.has_share = await extract_and_cache_shares(ctx.event, ctx.session_id)
    if not ctx.raw_msg and not ctx.has_share:
        return _SKIP
    return None


@stage("share_only_reply")
async def _stage_share_only(ctx: ChatContext) -> Optional[str]:
    if not ctx.raw_msg and ctx.has_share:
        recent = get_recent_shares(ctx.session_id)
        last_share = recent[-1] if recent else None
        # 图片分享走LLM回复流程，不在此阶段跳过
        if last_share and last_share.get("type") == "图片":
            return None
        if not ctx.is_group or ctx.event.is_tome() or random.random() < 0.3:
            if last_share and last_share.get("type") == "表情":
                await _handle_emoji_share(ctx, last_share)
            else:
                await _handle_link_share(ctx)
        return _SKIP
    return None


@stage("phone_control")
async def _stage_phone(ctx: ChatContext) -> Optional[str]:
    if not PHONE_CONTROL_ENABLED:
        return None
    if ctx.user_id != MY_QQ:
        return None
    try:
        from .phone_adb import execute_adb_command, check_device
        if check_device():
            result = execute_adb_command(ctx.raw_msg)
            if result:
                ctx.reply_text = result
                return _SKIP
        from .phone_control import execute_phone_command, is_phone_command
        if is_phone_command(ctx.raw_msg):
            result = await execute_phone_command(ctx.raw_msg)
            if result:
                ctx.reply_text = result
                return _SKIP
    except Exception as e:
        logger.warning(f"[手机] 控制模块异常: {e}")
    return None


@stage("group_filter")
async def _stage_group_filter(ctx: ChatContext) -> Optional[str]:
    if not ctx.is_group:
        return None

    # 始终响应: @我
    is_at_me = ctx.event.is_tome()
    if is_at_me:
        ctx.raw_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', ctx.raw_msg).strip()
        if not ctx.raw_msg:
            ctx.raw_msg = "在吗"
        return None

    # 昵称匹配
    nicknames = ["猫娘", "kitty", "喵喵", "在吗", "bot", "机器人"]
    if any(nick in ctx.raw_msg for nick in nicknames):
        return None

    # 气氛感知（替代简单的随机回复）
    from .group_atmosphere import should_join_conversation
    # 构造最近消息列表（简化版，从 session 获取）
    recent = [{"user_id": ctx.user_id, "timestamp": time.time()}]
    decision = should_join_conversation(recent, ctx.bot.self_id)

    if decision["should_reply"]:
        # 根据置信度决定是否回复
        if random.random() < decision["confidence"] * 0.5:
            logger.info(f"[群聊] 参与对话: {decision['reason']}")
            return None
    elif random.random() < RANDOM_REPLY_CHANCE:
        # 保留原有的小概率随机回复
        return None

    return _SKIP


@stage("xiaohaihe")
async def _stage_xiaohaihe(ctx: ChatContext) -> Optional[str]:
    shares_now = get_recent_shares(ctx.session_id)
    latest_share = shares_now[-1] if shares_now else None
    if latest_share and latest_share.get("needs_paste") and latest_share.get("platform") == "小黑盒":
        if ctx.raw_msg and len(ctx.raw_msg) > 100 and not any(kw in ctx.raw_msg for kw in ["讲了什么", "是什么", "怎么看", "这个呢"]):
            latest_share["summary"] = ctx.raw_msg[:2000]
            latest_share["needs_paste"] = False
            latest_share["restricted"] = False
            logger.info(f"[分享] 用户补充了小黑盒正文，长度: {len(ctx.raw_msg)}")
        elif any(kw in ctx.raw_msg for kw in ["讲了什么", "是什么", "内容", "说了什么", "这个呢", "怎么看", "分析一下", "评价"]):
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~")))
            return _SKIP
    return None


@stage("affection")
async def _stage_affection(ctx: ChatContext) -> Optional[str]:
    await apply_affection_delta(ctx.user_id, ctx.raw_msg)
    return None


@stage("context_analysis")
async def _stage_context(ctx: ChatContext) -> Optional[str]:
    ctx.recent_memories, ctx.relevant_tags, ctx.affection, ctx.mood, history_for_analysis = \
        await save_and_get_context_with_history(ctx.session_id, ctx.user_id, ctx.raw_msg)

    # === 简单消息：跳过深度分析，直接用默认值 ===
    from .schedule import get_schedule_state
    if ctx.complexity == "simple":
        from .context_analyzer import ContextAnalysis, EmotionState
        ctx.analysis = AnalysisResult(context=ContextAnalysis(), emotion=EmotionState())
        ctx.search_result = None
        ctx.world_context = ""
        ctx.bot_mood_result = {"dominant": "平静", "reason": ""}
        ctx.emotion_params = get_emotion_params(None)
        ctx.schedule = get_schedule_state()
        logger.info(f"[快速通道] 简单消息，跳过深度分析: {ctx.raw_msg[:20]}")
    else:
        # === 完整分析流程 ===
        async def _do_analysis():
            return await analyze_context_and_emotion(ctx.raw_msg, history_for_analysis, ctx.user_id)

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
            return await build_world_context_prompt(user_city)

        # 并行：分析 + 搜索 + 天气 + 提醒
        async def _do_reminders():
            return await get_pending_reminders_context(ctx.user_id)

        analysis, search_result, world_ctx, reminder_ctx = await asyncio.gather(
            _do_analysis(), _do_search(), _do_weather(), _do_reminders()
        )

        ctx.analysis = analysis
        ctx.search_result = search_result
        ctx.world_context = world_ctx
        ctx.reminder_context = reminder_ctx
        ctx.emotion_params = get_emotion_params(ctx.analysis.emotion)

        if ctx.analysis.context.referenced_entity:
            logger.info(f"[指代消解] 检测到指代: {ctx.analysis.context.referenced_entity}")

        # === 第一批并行：无依赖的 DB/状态查询 ===
        from .emotion_deep import get_emotion_memory_hint
        from .memory import get_shared_memory_hint, get_private_meme_hint, get_date_hint
        from .database import has_user_message_today, check_and_trigger_milestone

        async def _get_affection_decay():
            if ctx.affection and ctx.session_recovery:
                from .db_affection import get_affection_decay_hint
                return await get_affection_decay_hint(ctx.user_id)
            return None

        async def _get_undisclosed():
            if ctx.affection and random.random() < 0.15:
                from .db_session import get_undisclosed_facts
                return await get_undisclosed_facts(ctx.user_id, ctx.affection.get("score", 0))
            return None

        batch1_results = await asyncio.gather(
            update_bot_emotion(ctx.raw_msg, ctx.analysis.emotion),          # [0]
            get_emotion_memory_hint(ctx.user_id, ctx.raw_msg),              # [1]
            get_user_pref_hints(ctx.user_id),                               # [2]
            has_user_message_today(ctx.session_id),                         # [3]
            get_shared_memory_hint(ctx.user_id, ctx.raw_msg),               # [4]
            get_private_meme_hint(ctx.user_id, ctx.raw_msg),                # [5]
            get_date_hint(ctx.user_id),                                     # [6]
            check_and_trigger_milestone(ctx.user_id),                       # [7]
            _get_affection_decay(),                                         # [8]
            _get_undisclosed(),                                             # [9]
        )

        # 解包 batch1 结果
        ctx.bot_mood_result = batch1_results[0]
        ctx.emotion_memory_hint = batch1_results[1] or ""
        ctx.user_prefs = batch1_results[2]
        ctx.is_first_today = not batch1_results[3]
        ctx.shared_memory_hint = batch1_results[4] or ""
        ctx.private_meme_hint = batch1_results[5] or ""
        ctx.date_hint = batch1_results[6] or ""
        ctx.milestone_hint = batch1_results[7]
        ctx.affection_decay_hint = batch1_results[8]
        ctx.disclosure_hint = batch1_results[9]
        if ctx.disclosure_hint:
            from .database import mark_disclosed
            asyncio.create_task(mark_disclosed(ctx.user_id, ctx.disclosure_hint["key"]))

        # 情绪系统深化
        if ctx.bot_mood_result.get("recovery_stage"):
            ctx.emotion_recovery_hint = ctx.bot_mood_result["recovery_stage"]
        if ctx.bot_mood_result.get("swing_hint"):
            ctx.emotion_recovery_hint = ctx.bot_mood_result["swing_hint"]
        if ctx.bot_mood_result.get("contagion"):
            ctx.contagion_result = ctx.bot_mood_result["contagion"]
            ctx.bot_mood_result["valence"] = ctx.bot_mood_result.get("valence", 0) + ctx.contagion_result.get("valence_delta", 0)
            ctx.bot_mood_result["arousal"] = ctx.bot_mood_result.get("arousal", 0.2) + ctx.contagion_result.get("arousal_delta", 0)

        # === 同步计算（依赖 batch1 结果）===
        ctx.schedule = get_schedule_state()

        bot_mood_dominant = ctx.bot_mood_result.get("dominant", "平静") if ctx.bot_mood_result else "平静"

        # 对话节奏：话题桥接/过渡
        from .dialogue_rhythm import get_topic_bridge, get_topic_transition_hint
        prev_topic = ""
        if ctx.session_recovery:
            prev_topic = ctx.session_recovery.get("last_topic", "")
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

        # 行为模式
        from .behavior_engine import get_behavior_hint
        weather_condition = ""
        weather_temp = ""
        if ctx.world_context:
            weather_match = re.search(r'天气：(\S+)', ctx.world_context)
            if weather_match:
                weather_condition = weather_match.group(1)
            temp_match = re.search(r'(\d+)°C', ctx.world_context)
            if temp_match:
                weather_temp = temp_match.group(1)
        schedule_period = ctx.schedule.period if ctx.schedule else "active"
        ctx.behavior_hint = get_behavior_hint(weather_condition, weather_temp, schedule_period, bot_mood_dominant) or ""

        # === 第二批并行：依赖 batch1 结果 ===
        async def _get_icebreaker():
            if ctx.is_first_today and ctx.session_recovery:
                return await get_icebreaker_context(ctx.session_recovery, ctx.bot_mood_result) or ""
            return ""

        async def _get_group_social():
            if ctx.is_group:
                group_id = ctx.session_id.replace("group_", "")
                from .group_atmosphere import get_group_social_context
                from .db_group import update_member_activity
                social_ctx = await get_group_social_context(group_id, ctx.raw_msg)
                asyncio.create_task(update_member_activity(group_id, ctx.user_id))
                return social_ctx
            return {}

        async def _get_personalization():
            from .personalization import get_personalization_hints
            from .db_session import get_or_create_user_profile
            profile = await get_or_create_user_profile(ctx.user_id)
            custom_nickname = profile.get("nickname", "") if profile else ""
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
        )

        ctx.icebreaker_hint = batch2_results[0]
        social_ctx = batch2_results[1]
        if social_ctx:
            ctx.group_social_hint = social_ctx.get("social_hint", "")
            ctx.group_meme_hint = social_ctx.get("meme_hint", "")
            ctx.group_role_hint = social_ctx.get("role_hint", "")
        personal_hints = batch2_results[2]
        ctx.nickname_hint = personal_hints.get("nickname_hint", "")
        ctx.interest_hint = personal_hints.get("interest_hint", "")
        ctx.growth_hint = personal_hints.get("growth_hint", "")
        ctx.catchphrase_hint = personal_hints.get("catchphrase_hint", "")

    return None


@stage("schedule_interrupt")
async def _stage_schedule_interrupt(ctx: ChatContext) -> Optional[str]:
    """作息规律：根据时间决定是否中断消息处理。"""
    if not ctx.schedule:
        return None
    schedule = ctx.schedule

    # 凌晨 sleeping：30% 概率不回复
    if schedule.period == "sleeping" and random.random() < 0.3:
        logger.info("[作息] 深夜不回复（sleeping）")
        return _SKIP

    # 吃饭时间：15% 概率回"在吃饭"
    if schedule.period == "meal" and random.random() < 0.15:
        meal_msgs = ["在吃饭呢~等下聊", "先吃饭！", "等我吃完~", "正吃着呢~"]
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(random.choice(meal_msgs))))
        logger.info("[作息] 吃饭中断")
        return _SKIP

    return None


@stage("reminder")
async def _stage_reminder(ctx: ChatContext) -> Optional[str]:
    reminder_intent = is_reminder_request(ctx.raw_msg)
    if reminder_intent == "create":
        reply_text = await create_reminder(ctx.user_id, ctx.session_id, ctx.raw_msg)
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply_text)))
        return _SKIP
    elif reminder_intent == "list":
        reply_text = await list_reminders(ctx.user_id)
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply_text)))
        return _SKIP
    elif reminder_intent == "cancel":
        id_match = re.search(r'(\d+)', ctx.raw_msg)
        if id_match:
            reply_text = await cancel_reminder_by_id(ctx.user_id, int(id_match.group(1)))
        else:
            reply_text = await _generate_reminder_reply("no_reminder")
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply_text)))
        return _SKIP
    return None


@stage("llm_call")
async def _stage_llm(ctx: ChatContext) -> Optional[str]:
    shares_now = get_recent_shares(ctx.session_id)
    analysis_keywords = [
        "怎么看", "怎么讲", "分析一下", "评价", "观点", "有什么想法",
        "说说", "讲讲", "如何理解", "什么意思", "详细介绍", "详细说说"
    ]
    valid_shares = [s for s in shares_now if s.get("summary")]
    is_asking_analysis = any(kw in ctx.raw_msg for kw in analysis_keywords) and valid_shares

    if is_asking_analysis:
        analysis_prompt = build_analysis_prompt(valid_shares, ctx.raw_msg)
        if analysis_prompt == "[小黑盒内容需要用户粘贴正文后才能分析]":
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~")))
            return _SKIP

        length_info = {"target_lines": 4, "style": "专业分析+个性点评"}
        search_ctx = format_search_for_prompt(ctx.search_result) if ctx.search_result else ""
        sys_prompt = _build_system_prompt(
            ctx.affection, ctx.mood, length_info, ctx.relevant_tags, shares_now, ctx.raw_msg,
            context_analysis=ctx.analysis.context, emotion_state=ctx.analysis.emotion,
            search_context=search_ctx, reminder_context=ctx.reminder_context,
            world_context=ctx.world_context, bot_mood=ctx.bot_mood_result,
            user_prefs=ctx.user_prefs, session_recovery=ctx.session_recovery,
            disclosure_hint=ctx.disclosure_hint["text"] if ctx.disclosure_hint else None,
            affection_decay_hint=ctx.affection_decay_hint,
            milestone_hint=ctx.milestone_hint,
            schedule=ctx.schedule,
            voice_features=ctx.voice_features,
            shared_memory_hint=ctx.shared_memory_hint or None,
            private_meme_hint=ctx.private_meme_hint or None,
            date_hint=ctx.date_hint or None,
            topic_bridge=ctx.topic_bridge or None,
            icebreaker_hint=ctx.icebreaker_hint or None,
            topic_transition=ctx.topic_transition or None,
            emotion_recovery_hint=ctx.emotion_recovery_hint or None,
            emotion_memory_hint=ctx.emotion_memory_hint or None,
            group_social_hint=ctx.group_social_hint or None,
            group_meme_hint=ctx.group_meme_hint or None,
            group_role_hint=ctx.group_role_hint or None,
            behavior_hint=ctx.behavior_hint or None,
            nickname_hint=ctx.nickname_hint or None,
            interest_hint=ctx.interest_hint or None,
            growth_hint=ctx.growth_hint or None,
            catchphrase_hint=ctx.catchphrase_hint or None,
        )
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你的猫娘语气。绝对禁止括号动作描写。"
        messages = [{"role": "system", "content": sys_prompt}]
        for mem in ctx.recent_memories[-ANALYSIS_HISTORY_LIMIT:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(ctx.raw_msg, ctx.recent_memories, ctx.bot_mood_result)
        length_info["target_lines"] = parse_target_lines(ctx.emotion_params["target_lines"])
        ep = ctx.emotion_params
        if ep["temperature"] >= 1.0:
            length_info["style"] = "活泼轻快"
        elif ep["temperature"] <= 0.6:
            length_info["style"] = "冷淡简短"
        elif ep["temperature"] <= 0.7:
            length_info["style"] = "温柔低落"
        search_ctx = format_search_for_prompt(ctx.search_result) if ctx.search_result else ""
        sys_prompt = _build_system_prompt(
            ctx.affection, ctx.mood, length_info, ctx.relevant_tags, shares_now, ctx.raw_msg,
            context_analysis=ctx.analysis.context, emotion_state=ctx.analysis.emotion,
            search_context=search_ctx, reminder_context=ctx.reminder_context,
            world_context=ctx.world_context, bot_mood=ctx.bot_mood_result,
            user_prefs=ctx.user_prefs, session_recovery=ctx.session_recovery,
            disclosure_hint=ctx.disclosure_hint["text"] if ctx.disclosure_hint else None,
            affection_decay_hint=ctx.affection_decay_hint,
            milestone_hint=ctx.milestone_hint,
            schedule=ctx.schedule,
            voice_features=ctx.voice_features,
            shared_memory_hint=ctx.shared_memory_hint or None,
            private_meme_hint=ctx.private_meme_hint or None,
            date_hint=ctx.date_hint or None,
            topic_bridge=ctx.topic_bridge or None,
            icebreaker_hint=ctx.icebreaker_hint or None,
            topic_transition=ctx.topic_transition or None,
            emotion_recovery_hint=ctx.emotion_recovery_hint or None,
            emotion_memory_hint=ctx.emotion_memory_hint or None,
            group_social_hint=ctx.group_social_hint or None,
            group_meme_hint=ctx.group_meme_hint or None,
            group_role_hint=ctx.group_role_hint or None,
            behavior_hint=ctx.behavior_hint or None,
            nickname_hint=ctx.nickname_hint or None,
            interest_hint=ctx.interest_hint or None,
            growth_hint=ctx.growth_hint or None,
            catchphrase_hint=ctx.catchphrase_hint or None,
        )

        from .database import has_user_message_today
        greeting_type = detect_greeting_type(ctx.raw_msg, ctx.recent_memories)

        # 检查是否是晚安后又来聊天（5分钟内）
        is_comeback_after_farewell = False
        if greeting_type is None and greeting_type != "morning":
            from .database import get_last_farewell_time
            farewell_time = await get_last_farewell_time(ctx.session_id)
            if farewell_time:
                minutes_since = (time.time() - farewell_time) / 60
                if 0 < minutes_since < 5:
                    is_comeback_after_farewell = True

        if greeting_type == "morning":
            hour = datetime.now().hour
            time_hint = get_morning_time_hint(hour)
            is_first = not await has_user_message_today(ctx.session_id)
            greet_hint = f"\n【问候感知】用户在跟你说早安。当前时间 {hour}:00。{time_hint}"
            if is_first:
                greet_hint += " 这是用户今天第一条消息，可以自然地问候一下。"
            greet_hint += "\n回复要求：自然、口语化，不要每次都像客服一样'早安~今天也要元气满满哦'。根据时间调整语气。"
            sys_prompt += greet_hint
        elif greeting_type == "night":
            affection_hint = get_night_affection_hint(ctx.affection)
            farewell_confidence = is_night_farewell(ctx.raw_msg, ctx.recent_memories)["confidence"]

            if farewell_confidence >= 0.8:
                # 高置信度道别
                sys_prompt += (
                    f"\n【道别感知】用户在跟你说晚安/要睡了。{affection_hint}"
                    "\n回复要求：短、温暖、不要追问、不要开启新话题。"
                    "像关灯一样自然地道别。1句话就够了。"
                )
                # 记录道别时间
                from .database import record_farewell
                asyncio.create_task(record_farewell(ctx.user_id, ctx.session_id))
            else:
                # 低置信度（可能是抱怨）
                sys_prompt += (
                    f"\n【可能道别】用户说'困了'之类的，但不确定是否真要睡。{affection_hint}"
                    "\n回复要求：可以关心一下，但不要直接说晚安。"
                    "比如'那就早点休息呀~'、'困了就睡呗~'。如果他真要睡会再说的。"
                )
        elif is_comeback_after_farewell:
            # 晚安后又来聊天！
            sys_prompt += (
                "\n【调侃机会】用户刚才说了晚安/要睡了，结果又发消息了！"
                "这是一个很好的调侃机会。语气要调皮、得意。"
                "比如：'怎么还没睡？'、'不是说困了吗~'、'嘴上说睡了身体很诚实嘛~'"
                "\n回复要求：简短、调侃、不要太长。1-2句话。"
            )
        elif ctx.is_first_today:
            hour = datetime.now().hour
            if 6 <= hour < 12:
                sys_prompt += (
                    "\n【首条消息感知】这是用户今天第一条消息。"
                    "自然地问候一下，但不要刻意说'早安'，除非用户先说。"
                    "比如'你来啦~'、'今天这么早？'之类的自然过渡。"
                )
                from .database import log_proactive
                asyncio.create_task(log_proactive(
                    ctx.user_id, "private", "[感知式早安]", scene="morning_triggered"
                ))

        # 图片回复策略（P2）：基于人设的个性化图片回应（独立于上述条件）
        from .image_reply import get_image_reply_prompt
        image_shares = [s for s in shares_now if s.get("type") == "图片" and s.get("vision_text")]
        if image_shares:
            latest_image = image_shares[-1]
            image_type = latest_image.get("image_type", "unknown")
            vision_text = latest_image.get("vision_text", "")
            affection_score = ctx.affection.get("score", 0)
            image_prompt = get_image_reply_prompt(
                image_type, vision_text, affection_score, ctx.raw_msg, ctx.bot_mood_result
            )
            if image_prompt:
                sys_prompt += f"\n{image_prompt}"

        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER

        # 智能上下文选择（替代简单的保留最近N条）
        from .context_optimizer import select_context_messages, fit_messages_to_budget
        selected_memories = select_context_messages(ctx.recent_memories, ctx.raw_msg, history_limit)
        for mem in selected_memories:
            messages.append({"role": mem["role"], "content": mem["content"]})
        # 构造用户消息：纯图片时用图片描述代替空消息
        user_msg_content = ctx.raw_msg
        if not user_msg_content and image_shares:
            vision_desc = image_shares[-1].get("vision_text", "")
            user_msg_content = f"[发送了一张图片：{vision_desc[:200]}]"
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": user_msg_content})

        # Token 预算管理：确保不超出上下文窗口
        messages = fit_messages_to_budget(messages, sys_prompt)

    ctx.reply_text = await call_deepseek_api(
        messages,
        temperature=ctx.emotion_params["temperature"],
        task_type="chat",
        max_tokens=ctx.emotion_params["max_tokens"],
    )
    ctx.reply_text = filter_novel_actions(ctx.reply_text)
    return None


@stage("image_gen")
async def _stage_image_gen(ctx: ChatContext) -> Optional[str]:
    img_config = should_generate_image(ctx.raw_msg)
    if not img_config:
        return None
    if img_config["id"] == "draw":
        prompt = _extract_draw_prompt(ctx.raw_msg)
    else:
        prompt = img_config["prompt"]
    ctx.image_path = await generate_image(prompt)
    if ctx.image_path:
        logger.info(f"[图片] 准备发送: {ctx.image_path}")
    return None


@stage("plugins")
async def _stage_plugins(ctx: ChatContext) -> Optional[str]:
    for plugin in get_enabled_plugins():
        try:
            result = await plugin.on_message(ctx)
            if result is _SKIP:
                return _SKIP
        except Exception as e:
            logger.error(f"[插件] {plugin.meta.name} 执行失败: {e}")
    return None


@stage("humanize")
async def _stage_humanize(ctx: ChatContext) -> Optional[str]:
    if not ctx.reply_text:
        return None
    text = ctx.reply_text

    # 节奏增强：反应词前缀
    from .handler_humanize import maybe_add_reaction_prefix
    emotion_v = ctx.analysis.emotion.valence if ctx.analysis else 0.0
    emotion_a = ctx.analysis.emotion.arousal if ctx.analysis else 0.5
    text = maybe_add_reaction_prefix(text, emotion_v)

    # 原有人性化处理
    if random.random() < 0.03:
        text = introduce_typo(text)
    if random.random() < 0.02:
        text = introduce_mind_change(text)
    if random.random() < 0.01 and len(text) > 10:
        text = introduce_uncertainty(text)

    # 颜文字：根据情绪在句尾加表情符号
    emotion_dom = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else "平静"
    text = maybe_add_kaomoji(
        text,
        emotion_dominant=emotion_dom,
        emotion_valence=emotion_v,
        emotion_arousal=emotion_a,
        affection_score=ctx.affection.get("score", 0),
    )

    # 节奏增强：连发拆分
    from .handler_humanize import maybe_split_to_bursts
    bursts = maybe_split_to_bursts(text, emotion_a, emotion_v)
    if bursts:
        # 用换行连接，后续 split_long_reply 会拆成多条消息
        text = "\n".join(bursts)

    ctx.reply_text = text
    return None


@stage("post_process")
async def _stage_post(ctx: ChatContext) -> Optional[str]:
    await save_reply(ctx.session_id, ctx.user_id, ctx.raw_msg, ctx.reply_text)

    sticker_chance = ctx.emotion_params.get("sticker_chance", 0.25)
    reply_filtered, sticker_kept = filter_sticker_tag(ctx.reply_text, ctx.session_id, keep_probability=sticker_chance)
    sticker_scene = ""
    if sticker_kept:
        clean_text, sticker_emotion, sticker_scene = parse_sticker_tag(reply_filtered)
    else:
        clean_text = reply_filtered
        fallback_chance = sticker_chance * 0.6
        sticker_emotion = should_send_sticker_fallback(
            ctx.reply_text,
            ctx.analysis.emotion.dominant if ctx.analysis.emotion.confidence >= 0.4 else None,
            fallback_chance=fallback_chance,
        )

    text_for_links, reply_urls = split_reply_and_links(clean_text)
    search_items = extract_shareable_from_search(ctx.search_result) if ctx.search_result else []

    use_quote = should_quote(
        ctx.event, ctx.bot.self_id, ctx.raw_msg,
        ctx.is_group, ctx.is_explicit_search, ctx.recent_memories
    )
    first_sent = False

    # 打字延迟上下文（真人化：传入对方消息和复杂度）
    typing_ctx = {
        "is_first_reply": True,
        "is_question": "?" in ctx.raw_msg or "？" in ctx.raw_msg,
        "emotion_arousal": ctx.analysis.emotion.arousal if ctx.analysis else 0.5,
        "schedule_speed": ctx.schedule.reply_speed if ctx.schedule else 1.0,
        "is_quick_reply": ctx.complexity == "simple",
        "user_msg": ctx.raw_msg,                    # 对方消息，用于计算阅读时间
        "complexity": ctx.complexity,                # simple/normal/complex
        "is_night": ctx.schedule.period == "sleeping" if ctx.schedule else False,
    }

    send_as_voice = should_send_voice(ctx.raw_msg, clean_text, ctx.recent_memories)
    # === 发消息前取消"正在输入"状态（更自然：打完字→取消输入→发送）===
    await _set_typing_status(ctx.bot, ctx.event, False)
    if send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {clean_text[:30]}...")
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        await send_voice(ctx.bot, ctx.event, clean_text, emotion=voice_emotion)
        if reply_urls or search_items:
            rich_msg = build_rich_message("", reply_urls, search_items, show_links=ctx.is_explicit_search)
            if rich_msg:
                await asyncio.sleep(1.5)
                if use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, rich_msg))
                else:
                    await ctx.bot.send(ctx.event, rich_msg)
                first_sent = True
    else:
        logger.info(f"[决策] 上下文判断发文字: {clean_text[:30]}...")
        if reply_urls or search_items:
            rich_msg = build_rich_message(clean_text, reply_urls, search_items, show_links=ctx.is_explicit_search)
            parts = split_long_reply(str(rich_msg))
            for i, part in enumerate(parts):
                if i == 0:
                    # 首条：完整延迟（阅读+思考+打字）
                    await asyncio.sleep(calc_message_delay(part, typing_ctx))
                else:
                    # 追加：burst 延迟（2~5秒，模拟打完又想到要补）
                    typing_ctx["is_first_reply"] = False
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                if not first_sent and use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True
        else:
            parts = split_long_reply(clean_text)
            for i, part in enumerate(parts):
                if i == 0:
                    # 首条：完整延迟（阅读+思考+打字）
                    await asyncio.sleep(calc_message_delay(part, typing_ctx))
                else:
                    # 追加：burst 延迟（2~5秒）
                    typing_ctx["is_first_reply"] = False
                    await asyncio.sleep(random.uniform(2.0, 5.0))
                if not first_sent and use_quote:
                    await ctx.bot.send(ctx.event, make_quote_reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True

    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion, sticker_scene)
        if sticker_path:
            await asyncio.sleep(0.8)
            await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 发送: {sticker_emotion}|{sticker_scene} -> {os.path.basename(sticker_path)}")

    if ctx.image_path and os.path.exists(ctx.image_path):
        await asyncio.sleep(1.0)
        await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(ctx.image_path)))
        logger.info(f"[图片] 发送: {os.path.basename(ctx.image_path)}")

    # 随机行为（P2）：3% 概率突然分享点什么
    from .message_actions import maybe_share_something
    asyncio.create_task(maybe_share_something(ctx.bot, ctx.event, share_chance=0.03))

    # 情绪快照保存（P1）：会话结束时保存用户情绪供下次关心
    from .db_mood import save_mood_snapshot
    asyncio.create_task(save_mood_snapshot(ctx.user_id, ctx.session_id))

    return None


# ============================================================
# 辅助函数（分享快速回复）
# ============================================================

async def _handle_emoji_share(ctx: ChatContext, last_share: dict):
    emoji_text = last_share.get("summary", "")
    emoji_match = re.search(r'用户发送了(?:QQ表情|QQ商城表情|QQ内置表情|表情)[：:]?\s*(.+?)]', emoji_text)
    emoji_name = emoji_match.group(1).strip() if emoji_match else "表情"
    safe_emoji = emoji_name.replace("{", "").replace("}", "").replace("system", "").replace("assistant", "").replace("user", "")[:20]
    emotion_prompt = f"用户给你发了一个QQ表情「{safe_emoji}」，没有说其他话。"
    emoji_sys = (
        "你是一只猫娘，正在QQ上和人聊天。用户只给你发了一个表情，没有文字。"
        "根据表情的含义，用你的性格（猫系、会调侃、嘴硬、偶尔撒娇）回复1-2句。"
        "口语化、短句、像发QQ消息。不要加括号动作。"
        "如果适合发表情包，在末尾加 [sticker:情绪]（happy/angry/shy/sad/tsundere/cute/funny/love/speechless/excited）。大约20%概率加。绝对不要输出 [doge]、[微笑] 等QQ内置表情标签。"
    )
    messages = [
        {"role": "system", "content": emoji_sys},
        {"role": "user", "content": emotion_prompt}
    ]
    reply_text = await call_deepseek_api(messages, temperature=1.0)
    reply_text = filter_novel_actions(reply_text)
    clean_reply, sticker_kept = filter_sticker_tag(reply_text, ctx.session_id)
    emoji_scene = ""
    if sticker_kept:
        send_text, sticker_emotion, emoji_scene = parse_sticker_tag(clean_reply)
    else:
        send_text = clean_reply
        sticker_emotion = should_send_sticker_fallback(reply_text)
    if send_text.strip():
        parts = split_long_reply(send_text)
        for i, part in enumerate(parts):
            if i > 0:
                await asyncio.sleep(random.uniform(2.0, 5.0))
            if i == 0:
                await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(part)))
            else:
                await ctx.bot.send(ctx.event, Message(part))
    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion, emoji_scene)
        if sticker_path:
            await asyncio.sleep(random.uniform(1.0, 2.0))
            await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 回应表情: {sticker_emotion} -> {os.path.basename(sticker_path)}")
    await save_reply(ctx.session_id, ctx.user_id, f"[表情:{emoji_name}]", send_text)


async def _handle_link_share(ctx: ChatContext):
    share_sys = (
        "你是一只猫娘，正在QQ上和人聊天。用户给你发了一个链接/分享，没有说其他话。"
        "用你的性格（猫系、会调侃、嘴硬、偶尔撒娇、有点小好色）回复1句话，表示你看到了。"
        "口语化、短句、像发QQ消息。不要加括号动作。只输出回复内容。"
    )
    share_messages = [
        {"role": "system", "content": share_sys},
        {"role": "user", "content": "用户发了一个链接，没有其他文字。回复一句表示你看到了。"}
    ]
    try:
        share_reply = await call_deepseek_api(share_messages, temperature=1.0)
        share_reply = filter_novel_actions(share_reply).strip()
        if len(share_reply) > 3:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(share_reply)))
        else:
            raise ValueError("回复太短")
    except Exception:
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("喵？什么东西，让我看看~")))


# ============================================================
# 入口函数（执行 Pipeline）
# ============================================================

async def _set_typing_status(bot: Bot, event: MessageEvent, typing: bool):
    """设置"正在输入"状态。NapCat 扩展接口。

    event_type: 1=正在输入, 0=取消
    """
    try:
        user_id = event.user_id
        params = {"user_id": int(user_id), "event_type": 1 if typing else 0}
        await bot.call_api("set_input_status", **params)
    except Exception as e:
        logger.debug(f"[正在输入] 设置失败（可能不支持）: {e}")


async def handle_chat(bot: Bot, event: MessageEvent):
    """主入口：构建上下文并执行 Pipeline。"""
    from .performance_monitor import StageTimer, track_response
    start_time = time.time()

    # === 立刻发"正在输入"状态（不等延迟）===
    asyncio.create_task(_set_typing_status(bot, event, True))

    try:
        # 预检测消息中的图片和语音（用于消息分级）
        _msg_segments = event.get_message()
        _has_image = any(seg.type == "image" and seg.data.get("sub_type", 0) != 1 for seg in _msg_segments)
        _has_voice = any(seg.type == "record" for seg in _msg_segments)
        _raw_msg = _msg_segments.extract_plain_text().strip()

        ctx = ChatContext(
            bot=bot,
            event=event,
            raw_msg=_raw_msg,
            session_id=get_session_id(event),
            user_id=str(event.user_id),
            is_group=isinstance(event, GroupMessageEvent),
            complexity=classify_message_complexity(_raw_msg, _has_image, _has_voice),
        )

        for stage_name, stage_func in _PIPELINE:
            with StageTimer(stage_name):
                result = await stage_func(ctx)
            if result is _SKIP:
                return

    except Exception as e:
        import traceback
        logger.error(f"[handle_chat] 严重异常: {e}")
        traceback.print_exc()
        try:
            await bot.send(event, make_reply(event, Message("呜...脑袋有点乱，让我缓缓...")))
        except Exception:
            pass
    finally:
        total_ms = (time.time() - start_time) * 1000
        track_response(total_ms)
