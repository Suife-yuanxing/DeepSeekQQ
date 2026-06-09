"""主消息处理器 — Pipeline 架构。

借鉴 ECC 的 Hook 系统，将消息处理拆分为有序的 Pipeline 阶段。
每个阶段可短路（返回 SKIP 跳过后续），新增功能只需注册一个阶段。
"""
import asyncio
import os
import random
import re
import time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import List
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters.onebot.v11 import MessageSegment

from .api import call_deepseek_api
from .config import ANALYSIS_HISTORY_LIMIT
from .config import CHAT_HISTORY_MULTIPLIER
from .config import MY_QQ
from .config import PHONE_CONTROL_ENABLED
from .config import RANDOM_REPLY_CHANCE
from .config import REPLY_LENGTH_CONFIG
from .config import STT_ENABLED
from .context_analyzer import AnalysisResult
from .context_analyzer import analyze_context_and_emotion
from .context_analyzer import update_bot_emotion
from .handler_helpers import detect_greeting_type
from .handler_helpers import get_morning_time_hint
from .handler_helpers import get_night_affection_hint
from .handler_helpers import has_time_gap
from .handler_helpers import is_greeting
from .handler_helpers import is_multi_topic
from .handler_helpers import is_night_farewell
from .handler_helpers import is_question
from .handler_helpers import make_quote_reply

# 拆分出的子模块
from .handler_helpers import make_reply
from .handler_helpers import parse_target_lines
from .handler_helpers import should_quote
from .handler_humanize import introduce_mind_change
from .handler_humanize import introduce_typo
from .handler_humanize import introduce_uncertainty
from .handler_humanize import maybe_add_kaomoji
from .image_gen import _extract_draw_prompt
from .image_gen import generate_image
from .image_gen import should_generate_image
from .media import build_rich_message
from .media import extract_shareable_from_search
from .media import split_reply_and_links
from .memory import apply_affection_delta
from .memory import get_user_pref_hints
from .memory import recover_session_context
from .memory import save_and_get_context_with_history
from .memory import save_reply
from .plugin_manager import get_enabled_plugins
from .prompt import _build_system_prompt
from .prompt import estimate_reply_length
from .reminder import _generate_reminder_reply
from .reminder import cancel_reminder_by_id
from .reminder import create_reminder
from .reminder import get_pending_reminders_context
from .reminder import is_reminder_request
from .reminder import list_reminders
from .search import extract_search_query
from .search import format_search_for_prompt
from .search import search
from .search import should_search
from .security import get_blocked_reply
from .security import scan_input
from .share_parser import extract_and_cache_shares
from .share_parser import get_recent_shares
from .share_prompt import build_analysis_prompt
from .sticker import filter_sticker_tag
from .sticker import parse_sticker_tag
from .sticker import select_sticker_with_search
from .sticker import should_send_sticker_fallback
from .stt import recognize_voice
from .utils import calc_message_delay
from .utils import check_rate_limit
from .utils import filter_novel_actions
from .utils import get_session_id
from .utils import safe_task
from .utils import split_long_reply
from .voice import send_farewell_voice
from .voice import send_greeting_voice
from .voice import send_voice
from .voice import should_send_voice
from .voice import generate_voice_file
from .voice import _send_voice_file
from ._audio_utils import validate_file
from .voice_call import detect_voice_intent
from .voice_call import enter_voice_mode
from .voice_call import exit_voice_mode
from .voice_call import is_in_voice_mode
from .voice_call import touch_activity
from .world_context import build_world_context_prompt
from .world_context import extract_city_from_message

# 向后兼容：现有测试引用的内部函数名
_parse_target_lines = parse_target_lines
_reply = make_reply
_quote_reply = make_quote_reply


# ============================================================
# 情绪参数映射（温度/长度/表情包概率）
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
# 已读不回感知
# ============================================================

def _build_reply_gap_hint(gap_seconds: float, affection: dict, schedule, bot_mood: str, current_hour: int = -1) -> str:
    """根据 bot 最后回复到用户当前消息的时间间隔，生成已读不回提示。"""
    import random

    gap_min = gap_seconds / 60
    gap_hour = gap_min / 60
    affection_score = affection.get("score", 0) if affection else 0

    # 判断当前时段
    if current_hour < 0:
        from datetime import datetime
        current_hour = datetime.now().hour
    hour = current_hour
    is_late_night = 0 <= hour < 7  # 深夜到清晨，用户可能在睡觉

    # 深夜回来不算"已读不回"，走另一条路
    if is_late_night and gap_hour >= 3:
        if gap_hour >= 8:
            return random.choice([
                "用户可能刚睡醒，不要提间隔太久，自然地打招呼就好",
                "隔了很久才来消息，可能是刚起床，语气自然一些",
            ])
        return ""

    # 短间隔不触发
    if gap_min < 15:
        return ""

    # 根据好感度调整语气
    if affection_score >= 200:
        # 高好感度：撒娇式
        if 15 <= gap_min < 60:
            return random.choice([
                f"用户过了{int(gap_min)}分钟才回复，可以带点撒娇地说等了很久",
                f"过了{int(gap_min)}分钟才回我，稍微表达一下等待的小委屈",
            ])
        elif 1 <= gap_hour < 3:
            return random.choice([
                f"用户{int(gap_hour)}个多小时没回消息了，可以撒娇说\"终于回我了~\"",
                f"等了{int(gap_hour)}个多小时，语气有点委屈但不生气",
                "好久没回消息了，可以假装生气一下但不要太认真",
            ])
        elif 3 <= gap_hour < 8:
            return random.choice([
                "用户好几个小时没理我了，可以小声抱怨一下",
                "等了好几个小时，语气有点小委屈",
            ])
    elif affection_score >= 50:
        # 中好感度：关心式
        if 15 <= gap_min < 60:
            return random.choice([
                f"用户过了{int(gap_min)}分钟才回复，可以自然地接上话题",
                "隔了一会儿才回，语气自然不要刻意提",
            ])
        elif 1 <= gap_hour < 3:
            return random.choice([
                "用户1个多小时没回，可以问一下是不是在忙",
                "隔了挺久才回，自然地聊回来就好",
            ])
        elif 3 <= gap_hour < 8:
            return random.choice([
                "用户好几个小时没回，可以问问去干嘛了",
                "好几个小时没消息了，可以自然地说\"好久不见~\"",
            ])
    else:
        # 低好感度：平淡式
        if 1 <= gap_hour < 3:
            return random.choice([
                "用户隔了比较久才回复，正常接话就好",
                "过了一段时间才回，不用刻意提",
            ])
        elif gap_hour >= 3:
            return random.choice([
                "用户好几个小时没回，简单接话即可",
                "隔了很久，正常回复不要刻意提间隔",
            ])

    return ""
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
    # 已读不回感知
    reply_gap_hint: str = ""
    # 跨会话情绪记忆
    bot_emotion_memory_hint: str = ""
    # 群聊热度状态机
    group_heat_state: str = ""           # dormant / idle / active
    group_heat_description: str = ""     # 群活跃度自然语言描述
    # 对话疲劳感知
    fatigue_level: int = 0
    fatigue_hint: str = ""
    # 结构化天气数据（供行为引擎使用，避免 regex 解析格式化字符串）
    _weather_info: Any = None
    # 场景路由结果（供 prompt_templates 使用）
    scenes: list = field(default_factory=list)
    # 语音通话模式
    voice_mode: bool = False


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
    if ctx.session_recovery and ctx.session_recovery.get("bot_emotion_memory_hint"):
        ctx.bot_emotion_memory_hint = ctx.session_recovery["bot_emotion_memory_hint"]
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
            from .stt import _download_voice
            from .stt import _extract_voice_url
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


@stage("voice_call")
async def _stage_voice_call(ctx: ChatContext) -> Optional[str]:
    """语音通话模式：检测进入/退出意图，切换状态。

    仅私聊生效。群聊消息直接跳过。
    """
    if ctx.is_group:
        return None

    session_id = ctx.session_id
    intent = detect_voice_intent(ctx.raw_msg)

    if intent == "enter":
        enter_voice_mode(session_id)
        ctx.voice_mode = True
        # fire-and-forget 发接听语音
        from .utils import safe_task
        safe_task(send_greeting_voice(ctx.bot, ctx.event))
        if not ctx.raw_msg or len(ctx.raw_msg) <= 3:
            # 纯触发词（如"打电话"），不发额外回复
            return _SKIP
        # 触发词+内容（如"打电话 你在干嘛"），去掉触发词继续处理
        for kw in ["语音通话", "语音聊天", "接电话", "打电话", "开语音", "通话"]:
            if kw in ctx.raw_msg:
                ctx.raw_msg = ctx.raw_msg.replace(kw, "").strip()
                break

    elif intent == "exit":
        if exit_voice_mode(session_id):
            from .utils import safe_task
            safe_task(send_farewell_voice(ctx.bot, ctx.event))
        ctx.voice_mode = False
        if not ctx.raw_msg or len(ctx.raw_msg) <= 3:
            # 纯退出词（如"挂了"）
            return _SKIP

    elif is_in_voice_mode(session_id):
        # 已在语音模式中，更新活跃时间
        touch_activity(session_id)
        ctx.voice_mode = True

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
        # 视频平台分享（抖音/B站）：群聊中也总是回复，让bot主动分析视频
        is_video_share = (
            last_share and last_share.get("restricted")
            and last_share.get("platform") in ("douyin", "bilibili")
            and last_share.get("type") == "网页"
        )
        if is_video_share or not ctx.is_group or ctx.event.is_tome() or random.random() < 0.3:
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
        from .phone_adb import check_device
        from .phone_adb import execute_adb_command
        if check_device():
            result = execute_adb_command(ctx.raw_msg)
            if result:
                ctx.reply_text = result
                return _SKIP
        from .phone_control import execute_phone_command
        from .phone_control import is_phone_command
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

    # 群聊热度状态机：每条消息都会更新热度
    from .group_heat import heat_manager
    is_at_me = ctx.event.is_tome()
    heat_state = await heat_manager.on_message(ctx.session_id, is_at_bot=is_at_me)

    # 始终响应: @我
    if is_at_me:
        ctx.raw_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', ctx.raw_msg).strip()
        if not ctx.raw_msg:
            ctx.raw_msg = "在吗"
        # 将热度状态注入上下文，供 prompt 使用
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    # 昵称匹配
    nicknames = ["猫娘", "kitty", "喵喵", "bot", "机器人"]
    if any(nick in ctx.raw_msg for nick in nicknames):
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
        return None

    # 热度活跃状态下，有一定概率主动插话
    if heat_state == "active" and heat_manager.should_interject(ctx.session_id):
        logger.info(f"[群聊] 热度活跃插话 (heat={heat_manager.get_heat(ctx.session_id):.2f})")
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
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
            ctx.group_heat_state = heat_state
            ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
            return None
    elif random.random() < RANDOM_REPLY_CHANCE:
        # 保留原有的小概率随机回复
        ctx.group_heat_state = heat_state
        ctx.group_heat_description = heat_manager.get_activity_description(ctx.session_id)
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
    # 用户回复了，取消追问状态
    from .follow_up import record_user_reply
    record_user_reply(ctx.session_id)

    ctx.recent_memories, ctx.relevant_tags, ctx.affection, ctx.mood, history_for_analysis = \
        await save_and_get_context_with_history(ctx.session_id, ctx.user_id, ctx.raw_msg)

    # 话题追踪：在 memories 加载后注入话题上下文（避免 session_recovery 阶段 memories 为空）
    from .topic_tracker import get_topic_context
    topic_context = get_topic_context(ctx.session_id, ctx.recent_memories)
    if topic_context:
        if not ctx.session_recovery:
            ctx.session_recovery = {}
        ctx.session_recovery["topic_context"] = topic_context
        logger.debug(f"[话题追踪] 注入话题上下文: {topic_context[:50]}...")

    # === 简单消息：跳过深度分析，直接用默认值 ===
    from .schedule import get_schedule_state
    if ctx.complexity == "simple":
        from .context_analyzer import ContextAnalysis
        from .context_analyzer import EmotionState
        ctx.analysis = AnalysisResult(context=ContextAnalysis(), emotion=EmotionState())
        ctx.search_result = None
        ctx.world_context = ""
        ctx.bot_mood_result = {"dominant": "平静", "reason": ""}
        ctx.emotion_params = get_emotion_params(None)
        ctx.schedule = get_schedule_state()
        logger.info(f"[快速通道] 简单消息，跳过深度分析: {ctx.raw_msg[:20]}")
    else:
        await _run_full_analysis(ctx, history_for_analysis)

    return None


async def _run_full_analysis(ctx: ChatContext, history_for_analysis: list):
    """完整分析流程：拆分为核心分析 → 状态查询 → 同步计算 → 深化查询 → 疲劳感知。"""
    await _run_core_analysis(ctx, history_for_analysis)
    await _run_batch1_queries(ctx)
    bot_mood_dominant = _run_sync_computations(ctx)
    await _run_batch2_queries(ctx, bot_mood_dominant)
    await _run_fatigue_and_gap(ctx, bot_mood_dominant)


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
        from .world_context import get_weather
        weather_info = await get_weather(user_city)
        if weather_info:
            weather_info._city = user_city
            ctx._weather_info = weather_info
        return await build_world_context_prompt(user_city)

    async def _do_reminders():
        return await get_pending_reminders_context(ctx.user_id)

    results = await asyncio.gather(
        _do_analysis(), _do_search(), _do_weather(), _do_reminders(),
        return_exceptions=True
    )
    analysis, search_result, world_ctx, reminder_ctx = results

    # 处理并行任务中的异常：失败的任务用默认值，不中断 pipeline
    if isinstance(analysis, Exception):
        logger.error(f"[分析] 情绪/上下文分析失败: {analysis}")
        from .context_analyzer import ContextAnalysis
        from .context_analyzer import EmotionState
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
    from .database import check_and_trigger_milestone
    from .database import has_user_message_today
    from .emotion_deep import get_emotion_memory_hint
    from .memory import get_date_hint
    from .memory import get_private_meme_hint
    from .memory import get_shared_memory_hint

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
        from .database import mark_disclosed
        safe_task(mark_disclosed(ctx.user_id, ctx.disclosure_hint["key"]))

    # 情绪系统深化
    if ctx.bot_mood_result.get("recovery_stage"):
        ctx.emotion_recovery_hint = ctx.bot_mood_result["recovery_stage"]
    if ctx.bot_mood_result.get("swing_hint"):
        ctx.emotion_recovery_hint = ctx.bot_mood_result["swing_hint"]
    if ctx.bot_mood_result.get("contagion"):
        ctx.contagion_result = ctx.bot_mood_result["contagion"]

    # 情绪因果链：最近情绪变化趋势
    from .context_analyzer import get_emotion_cause_chain
    cause_chain = await get_emotion_cause_chain(ctx.user_id)
    if cause_chain:
        ctx.emotion_memory_hint = (ctx.emotion_memory_hint or "") + f"\n情绪变化趋势：{cause_chain}"
        ctx.bot_mood_result["valence"] = ctx.bot_mood_result.get("valence", 0) + ctx.contagion_result.get("valence_delta", 0)
        ctx.bot_mood_result["arousal"] = ctx.bot_mood_result.get("arousal", 0.2) + ctx.contagion_result.get("arousal_delta", 0)


def _run_sync_computations(ctx: ChatContext) -> str:
    """同步计算：作息、对话节奏、行为模式。返回 bot_mood_dominant。"""
    from .schedule import get_schedule_state
    ctx.schedule = get_schedule_state()
    bot_mood_dominant = ctx.bot_mood_result.get("dominant", "平静") if ctx.bot_mood_result else "平静"

    # 对话节奏：话题桥接/过渡
    from .dialogue_rhythm import get_icebreaker_context
    from .dialogue_rhythm import get_topic_bridge
    from .dialogue_rhythm import get_topic_transition_hint
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

    # 场景路由（prompt_templates 集成）
    try:
        from .prompt_templates import classify_scenes
        from .handler_helpers import is_question
        from .handler_helpers import is_greeting
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
    from .behavior_engine import get_behavior_hint
    weather_condition = ""
    weather_temp = ""
    user_city = ""
    if ctx._weather_info:
        weather_condition = ctx._weather_info.condition or ""
        weather_temp = ctx._weather_info.temp or ""
        user_city = getattr(ctx._weather_info, '_city', "") or ""
    schedule_period = ctx.schedule.period if ctx.schedule else "active"
    ctx.behavior_hint = get_behavior_hint(
        weather_condition, weather_temp, schedule_period, bot_mood_dominant, city=user_city
    ) or ""

    return bot_mood_dominant


async def _run_batch2_queries(ctx: ChatContext, bot_mood_dominant: str):
    """第三批并行：依赖 batch1 结果的查询（破冰/群聊社交/个性化）。"""
    async def _get_icebreaker():
        if ctx.is_first_today and ctx.session_recovery:
            return await get_icebreaker_context(ctx.session_recovery, ctx.bot_mood_result) or ""
        return ""

    async def _get_group_social():
        if ctx.is_group:
            group_id = ctx.session_id.replace("group_", "")
            from .db_group import update_member_activity
            from .group_atmosphere import get_group_social_context
            social_ctx = await get_group_social_context(group_id, ctx.raw_msg)
            safe_task(update_member_activity(group_id, ctx.user_id))
            return social_ctx
        return {}

    async def _get_personalization():
        from .db_session import get_or_create_user_profile
        from .personalization import get_personalization_hints
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
    from .conversation_fatigue import analyze_conversation_fatigue
    fatigue_result = analyze_conversation_fatigue(ctx.recent_memories, ctx.raw_msg, ctx.schedule)
    ctx.fatigue_level = fatigue_result["level"]
    ctx.fatigue_hint = fatigue_result["hint"]
    if ctx.fatigue_level >= 2:
        logger.info(f"[疲劳感知] level={ctx.fatigue_level} score={fatigue_result['score']} signals={fatigue_result['signals']}")

    # 已读不回感知
    import time as _time

    from .db_memories import get_last_bot_reply_time
    last_bot_ts = await get_last_bot_reply_time(ctx.session_id)
    if last_bot_ts > 0:
        gap_seconds = _time.time() - last_bot_ts
        ctx.reply_gap_hint = _build_reply_gap_hint(
            gap_seconds, ctx.affection, ctx.schedule, bot_mood_dominant
        )


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
    from .config import REMINDER_ENABLED
    if not REMINDER_ENABLED:
        return None
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


@stage("music")
async def _stage_music(ctx: ChatContext) -> Optional[str]:
    """音乐意图检测与处理（点歌、推荐、歌词展示）。"""
    from .music import handle_music_stage
    result = await handle_music_stage(ctx)
    return _SKIP if result == "SKIP" else None


@stage("llm_call")
async def _stage_llm(ctx: ChatContext) -> Optional[str]:
    shares_now = get_recent_shares(ctx.session_id)

    # 场景路由 — 构建场景提示（prompt_templates 集成）
    scene_hint = ""
    if ctx.scenes:
        try:
            from .prompt_templates import get_scene_templates
            from .prompt_templates import get_template
            template_names = get_scene_templates(ctx.scenes)
            extra_hints = []
            for name in template_names:
                if name in ("greeting_mode", "emotional_mode", "question_mode"):
                    content = get_template(name)
                    if content:
                        extra_hints.append(content)
            if extra_hints:
                scene_hint = "\n".join(extra_hints)
        except Exception:
            pass

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
            reply_gap_hint=ctx.reply_gap_hint or None,
            bot_emotion_memory_hint=ctx.bot_emotion_memory_hint or None,
            fatigue_hint=ctx.fatigue_hint or None,
            group_heat_desc=ctx.group_heat_description or None,
            scene_hint=scene_hint or None,
        )
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你的猫娘语气。绝对禁止括号动作描写。"
        messages = [{"role": "system", "content": sys_prompt}]
        for mem in ctx.recent_memories[-ANALYSIS_HISTORY_LIMIT:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(ctx.raw_msg, ctx.recent_memories, ctx.bot_mood_result)
        length_info["target_lines"] = parse_target_lines(ctx.emotion_params["target_lines"])

        # 活跃度修正：根据情绪/作息动态调整回复长度
        from .behavior_engine import get_verbosity_modifier
        schedule_period = ctx.schedule.period if ctx.schedule else "active"
        bot_mood_dom = ctx.bot_mood_result.get("dominant", "平静") if ctx.bot_mood_result else "平静"
        verbosity = get_verbosity_modifier(schedule_period, bot_mood_dom)
        length_info["target_lines"] = max(1, round(length_info["target_lines"] * verbosity))
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
            reply_gap_hint=ctx.reply_gap_hint or None,
            bot_emotion_memory_hint=ctx.bot_emotion_memory_hint or None,
            fatigue_hint=ctx.fatigue_hint or None,
            group_heat_desc=ctx.group_heat_description or None,
            scene_hint=scene_hint or None,
        )

        # 语音通话模式：注入口语化 prompt
        if ctx.voice_mode:
            sys_prompt += (
                "\n【语音通话模式】你现在正在和用户进行语音通话，你的回复会被转成语音说出去。\n"
                "回复应该更像真实的口语对话：更短的句子、更自然的语气、更多的语气词。\n"
                "想象你真的在打电话，回复就像你在电话里会说出来的话。\n"
                "不要长篇大论，不要结构化分析，不要括号动作描写。\n"
                "控制在2-3句话以内，像正常人打电话一样的节奏。"
            )

        from .database import has_user_message_today
        greeting_type = detect_greeting_type(ctx.raw_msg, ctx.recent_memories)

        # 检查是否是道别后又来聊天（5分钟内）
        is_comeback_after_farewell = False
        if greeting_type is None:
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
                safe_task(record_farewell(ctx.user_id, ctx.session_id))
                # 取消追问（修复：说了晚安后追问系统还在追问）
                from .follow_up import suppress_followup
                suppress_followup(ctx.session_id)
                logger.info(f"[晚安] 取消追问 session={ctx.session_id[:8]}")
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
                safe_task(log_proactive(
                    ctx.user_id, "private", "[感知式早安]", scene="morning_triggered"
                ))

        # 图片回复策略（P2）：基于人设的个性化图片回应（独立于上述条件）
        from .image_reply import get_image_reply_prompt
        from .image_reply import is_emotional_share
        from .image_reply import should_analyze_in_detail
        image_shares = [s for s in shares_now if s.get("type") == "图片" and s.get("vision_text")]
        if image_shares:
            latest_image = image_shares[-1]
            image_type = latest_image.get("image_type", "unknown")
            vision_text = latest_image.get("vision_text", "")
            affection_score = ctx.affection.get("score", 0)

            # 判断图片分析深度和回复策略
            detailed = should_analyze_in_detail(ctx.raw_msg, len(image_shares))
            emotional = is_emotional_share(ctx.raw_msg)

            image_prompt = get_image_reply_prompt(
                image_type, vision_text, affection_score, ctx.raw_msg, ctx.bot_mood_result
            )
            if image_prompt:
                if detailed:
                    image_prompt += "\n用户希望你详细分析这张图片，多说一些。"
                elif emotional:
                    image_prompt += "\n用户在分享有趣/好看的内容，附和一下就好，不用分析。"
                sys_prompt += f"\n{image_prompt}"

        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER

        # 智能上下文选择（替代简单的保留最近N条）
        from .context_optimizer import fit_messages_to_budget
        from .context_optimizer import select_context_messages
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

        # Token 预算管理：先尝试语义压缩，再硬截断
        from .context_compressor import compress_context
        from .context_compressor import estimate_messages_tokens
        est_tokens = estimate_messages_tokens(messages)
        if est_tokens > 512:
            messages, compressed = await compress_context(
                ctx.session_id, messages, call_deepseek_api
            )
            if compressed:
                logger.debug(f"[上下文] 语义压缩完成 session={ctx.session_id[:20]}...")
        messages = fit_messages_to_budget(messages, sys_prompt)

        # 上下文优化统计（调试用）
        from .context_optimizer import get_context_stats
        ctx_stats = get_context_stats(ctx.recent_memories, selected_memories, sys_prompt)
        if ctx_stats.get("token_saved", 0) > 0:
            logger.debug(f"[上下文] 压缩率={ctx_stats['compression_ratio']:.1%} 节省={ctx_stats['token_saved']}tokens")

    try:
        ctx.reply_text = await call_deepseek_api(
            messages,
            temperature=ctx.emotion_params["temperature"],
            task_type="chat",
            max_tokens=ctx.emotion_params["max_tokens"],
        )
    except Exception as e:
        logger.error(f"[LLM] API 调用失败: {e}")
        ctx.reply_text = "抱歉，我现在脑子有点转不过来，稍后再聊好吗？"
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

    # 节奏增强：反应词前缀（上下文感知版）
    from .handler_humanize import maybe_add_reaction_prefix
    emotion_v = ctx.analysis.emotion.valence if ctx.analysis else 0.0
    emotion_a = ctx.analysis.emotion.arousal if ctx.analysis else 0.5
    emotion_dom = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else "平静"

    # 传入用户消息和情绪，启用上下文感知反应词
    text = maybe_add_reaction_prefix(
        text, emotion_v,
        user_message=ctx.raw_msg,
        emotion=emotion_dom
    )

    # 原有人性化处理
    if random.random() < 0.03:
        text = introduce_typo(text)
    if random.random() < 0.02:
        text = introduce_mind_change(text)
    if random.random() < 0.01 and len(text) > 10:
        text = introduce_uncertainty(text)

    # 颜文字：根据情绪在句尾加表情符号
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
    await save_reply(ctx.session_id, ctx.user_id, ctx.raw_msg, ctx.reply_text, ctx.bot_mood_result)

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

    send_as_voice = should_send_voice(ctx.raw_msg, clean_text, ctx.recent_memories, voice_mode=ctx.voice_mode)
    voice_max_len = 200 if ctx.voice_mode else 0

    # === 发消息前取消"正在输入"状态（更自然：打完字→取消输入→发送）===
    await _set_typing_status(ctx.bot, ctx.event, False)

    # 语音模式：优先发语音，失败才回退文字
    if ctx.voice_mode:
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        voice_path = await generate_voice_file(clean_text, emotion=voice_emotion, max_length=200)
        if voice_path and validate_file(voice_path, 100):
            logger.info(f"[语音通话] 发送语音: {clean_text[:30]}...")
            await _send_voice_file(ctx.bot, ctx.event, voice_path)
            # 语音模式下纯语音，不发送文字/链接/表情包/图片
            return
        else:
            logger.warning(f"[语音通话] 语音生成失败，回退到文字: {clean_text[:30]}...")
            # 继续下面的文字发送流程
    elif send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {clean_text[:30]}...")
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        await send_voice(ctx.bot, ctx.event, clean_text, emotion=voice_emotion, max_length=voice_max_len)
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

    # 记录bot消息类型，用于追问系统
    from .follow_up import classify_bot_message
    from .follow_up import record_bot_message
    if ctx.reply_text and not ctx.is_group:
        msg_type = classify_bot_message(ctx.reply_text)
        record_bot_message(ctx.session_id, ctx.reply_text, msg_type)

        # 晚安关键词检测：bot 回复包含晚安关键词时自动取消追问
        night_keywords = ["晚安", "快睡", "去睡", "睡觉吧", "好梦", "明天见"]
        if any(kw in ctx.reply_text for kw in night_keywords):
            from .follow_up import suppress_followup
            suppress_followup(ctx.session_id)
            logger.info(f"[晚安] bot回复包含晚安关键词，取消追问 session={ctx.session_id[:8]}")

    # === 对话疲劳：抑制追问 + 自然收尾 ===
    if ctx.fatigue_level >= 2 and not ctx.is_group:
        from .follow_up import suppress_followup
        suppress_followup(ctx.session_id)
        logger.info(f"[疲劳感知] 抑制追问 | level={ctx.fatigue_level}")

    if ctx.fatigue_level >= 3 and not ctx.is_group:
        from .conversation_fatigue import get_closing_message
        closing = get_closing_message(ctx.fatigue_level, ctx.schedule)
        if closing:
            await asyncio.sleep(random.uniform(1.5, 3.0))
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(closing)))
            logger.info(f"[疲劳感知] 发送收尾消息: {closing}")

    # 随机行为（P2）：3% 概率突然分享点什么
    from .message_actions import maybe_share_something
    safe_task(maybe_share_something(ctx.bot, ctx.event, share_chance=0.03))

    # 情绪快照保存（P1）：会话结束时保存用户情绪供下次关心
    from .db_mood import save_mood_snapshot
    safe_task(save_mood_snapshot(ctx.user_id, ctx.session_id))

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
    await save_reply(ctx.session_id, ctx.user_id, f"[表情:{emoji_name}]", send_text, ctx.bot_mood_result)


async def _handle_link_share(ctx: ChatContext):
    # 获取分享内容
    recent = get_recent_shares(ctx.session_id)
    last_share = recent[-1] if recent else None

    # 构建分享内容描述
    share_desc = ""
    fetch_failed = False

    if last_share:
        share_type = last_share.get("type", "链接")
        share_source = last_share.get("source", "")
        share_summary = last_share.get("summary", "")
        fetch_failed = last_share.get("fetch_failed", False)

        if fetch_failed:
            share_desc = f"用户发了一个{share_type}链接，但内容无法读取。"
        elif share_summary and len(share_summary) > 10:
            share_desc = f"用户发了一个{share_type}：{share_source}\n内容摘要：{share_summary[:300]}"
        else:
            share_desc = f"用户发了一个{share_type}链接：{share_source}"
    else:
        share_desc = "用户发了一个链接，没有其他文字。"

    # 构建系统提示
    # 判断是否为视频平台分享（需主动讨论而非仅确认）
    is_video_share = (
        last_share and last_share.get("restricted")
        and last_share.get("platform") in ("douyin", "bilibili")
    )

    if is_video_share:
        share_sys = (
            "你是一只猫娘，正在QQ上和人聊天。用户给你发了一个视频分享，没有说其他话。"
            "用你的性格（猫系、会调侃、嘴硬、偶尔撒娇、有点小好色）回复1-3句话，"
            "主动评论/吐槽/讨论这个视频的内容（基于标题和描述）。"
            "不要只说「看到了」「收到」「让我看看」这种废话，要说点有内容的。"
            "口语化、短句、像发QQ消息。不要加括号动作。只输出回复内容。"
        )
    else:
        share_sys = (
            "你是一只猫娘，正在QQ上和人聊天。用户给你发了一个链接/分享，没有说其他话。"
            "用你的性格（猫系、会调侃、嘴硬、偶尔撒娇、有点小好色）回复1句话，表示你看到了。"
            "口语化、短句、像发QQ消息。不要加括号动作。只输出回复内容。"
        )

    # 如果内容无法读取，添加反编造规则
    if fetch_failed:
        share_sys += (
            "\n\n重要：这个链接的内容无法读取（可能是视频或需要登录）。"
            "你没有看到任何内容，所以绝对不要编造内容！"
            "直接说「我这边打不开这个链接诶」「没看到内容哦」或类似的话。"
        )

    share_messages = [
        {"role": "system", "content": share_sys},
        {"role": "user", "content": share_desc}
    ]
    try:
        share_reply = await call_deepseek_api(share_messages, temperature=1.0)
        share_reply = filter_novel_actions(share_reply).strip()
        if len(share_reply) > 3:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(share_reply)))
        else:
            raise ValueError("回复太短")
    except Exception:
        if fetch_failed:
            await ctx.bot.send(ctx.event, make_reply(ctx.event, Message("我这边打不开这个链接诶")))
        else:
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
    from .performance_monitor import StageTimer
    from .performance_monitor import track_response
    start_time = time.time()

    # === 立刻发"正在输入"状态（不等延迟）===
    safe_task(_set_typing_status(bot, event, True))

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
        logger.exception(f"[handle_chat] 严重异常: {e}")
        try:
            await bot.send(event, make_reply(event, Message("呜...脑袋有点乱，让我缓缓...")))
        except Exception:
            pass
    finally:
        total_ms = (time.time() - start_time) * 1000
        track_response(total_ms)
