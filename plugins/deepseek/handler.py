"""主消息处理器 — Pipeline 架构。

借鉴 ECC 的 Hook 系统，将消息处理拆分为有序的 Pipeline 阶段。
每个阶段可短路（返回 SKIP 跳过后续），新增功能只需注册一个阶段。
"""
import os
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
from .memory import save_and_get_context, save_reply, apply_affection_delta, save_and_get_context_with_history, get_user_pref_hints
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


# ============================================================
# 情绪驱动参数映射（功能⑤）
# ============================================================

def get_emotion_params(emotion) -> dict:
    """根据 EmotionState 的 VA 值返回行为参数。

    映射规则：
    | 情绪状态     | max_tokens | temperature | sticker_chance | target_lines |
    |-------------|-----------|-------------|---------------|-------------|
    | 兴奋 V>0.5,A>0.7 | 1800 | 1.1 | 0.50 | 4-5 |
    | 开心 V>0.3,A>0.5 | 1500 | 1.0 | 0.40 | 3-4 |
    | 生气 V<-0.5,A>0.5 |  600 | 0.6 | 0.05 | 1   |
    | 难过 V<-0.3       |  800 | 0.7 | 0.10 | 1-2 |
    | 平静 A<0.3        | 1200 | 0.8 | 0.20 | 2-3 |
    | 害羞 V>0,A>0.5    | 1000 | 0.9 | 0.30 | 2   |
    | 默认              | 1500 | 0.9 | 0.25 | 2-4 |
    """
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
# 辅助：构造引用回复消息
# ============================================================

def _reply(event: MessageEvent, msg: Message) -> Message:
    """给消息加上引用回复 segment（仅第一条消息使用）。"""
    return Message(MessageSegment.reply(event.message_id)) + msg


# ============================================================
# Pipeline 数据结构
# ============================================================

_SKIP = object()  # 短路标记：跳过后续阶段


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
    # 分析结果
    analysis: Optional[Any] = None
    bot_mood_result: Optional[dict] = None
    recent_memories: list = field(default_factory=list)
    relevant_tags: list = field(default_factory=list)
    affection: dict = field(default_factory=dict)
    mood: dict = field(default_factory=dict)
    # 搜索
    search_result: Optional[Any] = None
    is_explicit_search: bool = False
    # 上下文
    reminder_context: str = ""
    world_context: str = ""
    # 回复
    reply_text: str = ""
    # 情绪驱动参数（功能⑤）
    emotion_params: dict = field(default_factory=lambda: {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"})
    # 用户偏好（功能③）
    user_prefs: dict = field(default_factory=dict)
    # 图片生成（功能④）
    image_path: str = ""


def _is_multi_topic(msg: str) -> bool:
    """判断消息是否包含多个独立话题（用句号/换行/问号分隔的多个语句）。"""
    # 按中文句号、问号、感叹号、换行分割
    segments = re.split(r'[。！？\n]+', msg)
    # 过滤空白段，保留有意义的
    meaningful = [s.strip() for s in segments if len(s.strip()) >= 4]
    return len(meaningful) >= 2


def _is_question(msg: str) -> bool:
    """判断消息是否是一个明确的提问。"""
    if msg.rstrip().endswith(('?', '？')):
        return True
    q_keywords = ["怎么", "为什么", "什么", "哪里", "哪个", "多少", "几",
                  "吗", "呢", "能不能", "可以吗", "好不好", "是不是", "有没有"]
    return any(kw in msg for kw in q_keywords)


def _is_greeting(msg: str) -> bool:
    """判断消息是否是简单寒暄（不需要引用）。"""
    greetings = ["嗯", "嗯嗯", "哈哈", "哦", "好的", "好吧", "行", "可以",
                 "ok", "OK", "收到", "了解", "知道了", "嗯好", "好嘞", "棒"]
    return msg.strip() in greetings


def _detect_greeting_type(msg: str) -> Optional[str]:
    """检测问候类型，返回 'morning'/'night'/None。"""
    morning_kw = ["早安", "早", "早上好", "早呀", "早啊", "good morning", "起床"]
    if any(kw in msg for kw in morning_kw):
        return "morning"
    night_kw = ["晚安", "睡了", "困了", "要睡了", "好困", "晚安安",
                "good night", "拜拜", "明天见", "下了", "睡觉"]
    if any(kw in msg for kw in night_kw):
        return "night"
    return None


def _get_morning_time_hint(hour: int) -> str:
    """根据时间返回差异化早安语气提示。"""
    if 6 <= hour < 7:
        return "用户起得很早，语气带点惊讶和关心，比如'这么早？'"
    elif 7 <= hour < 8:
        return "早上时段，语气元气、活泼"
    elif 8 <= hour < 9:
        return "正常早安时间，自然问候"
    elif 9 <= hour < 10:
        return "用户起得比较晚，可以调侃一句，比如'终于醒了？'"
    return ""


def _get_night_affection_hint(affection: dict) -> str:
    """根据好感度返回晚安语气提示。"""
    score = affection.get("score", 0) if affection else 0
    if score >= 200:
        return "关系亲密，可以暧昧一点，比如'梦里见~'"
    elif score >= 50:
        return "关系不错，温暖道别"
    return "关系一般，简洁晚安即可"


def _has_time_gap(ctx: ChatContext, threshold: int = 300) -> bool:
    """检查与上一条消息的时间间隔是否超过阈值。"""
    import time
    if not ctx.recent_memories:
        return False
    last = ctx.recent_memories[-1]
    if isinstance(last, dict) and last.get("timestamp"):
        try:
            return (time.time() - float(last["timestamp"])) > threshold
        except (ValueError, TypeError):
            pass
    return False


def _is_bot_at(event: MessageEvent, bot_id: str) -> bool:
    """检查 bot 是否被 @ 了。"""
    try:
        for seg in event.message:
            if seg.type == "at" and str(seg.data.get("qq", "")) == str(bot_id):
                return True
    except Exception:
        pass
    return False


def _should_quote(ctx: ChatContext) -> bool:
    """模拟真人引用决策：只在需要定位上下文时引用。

    核心原则：引用是「定位器」，帮对方知道我在回哪句。
    - 群聊 → 条件引用
    - 私聊 → 永不引用（上下文天然清晰，两个人聊不需要定位）
    """
    msg = ctx.raw_msg.strip()

    # 私聊不引用
    if not ctx.is_group:
        return False

    # ===== 群聊：始终引用的场景 =====
    if ctx.is_explicit_search:
        return True
    analysis_keywords = ["怎么看", "分析", "评价", "观点", "说说", "讲讲", "详细介绍"]
    if any(kw in msg for kw in analysis_keywords):
        return True

    # ===== 群聊：始终不引用的场景 =====
    if _is_greeting(msg):
        return False

    # ===== 群聊：条件引用 =====
        # 被 @ → 引用（必须让对方知道在回谁）
        if _is_bot_at(ctx.event, ctx.bot.self_id):
            return True
        # 多话题消息 → 引用相关部分（帮用户定位 bot 回的是哪句）
        if _is_multi_topic(msg):
            return True
        # 时间间隔 > 5 分钟 → 引用（上下文已断，需要定位）
        if _has_time_gap(ctx):
            return True
        # 明确提问 → 引用（方便回看问题）
        if _is_question(msg):
            return True
        # 连续聊天 → 不引用
        return False


# ============================================================
# 辅助：解析 target_lines 范围字符串
# ============================================================

def _parse_target_lines(expr: str) -> int:
    """将 '4-5' / '1' / '2-4' 等字符串解析为随机整数。"""
    if "-" in expr:
        parts = expr.split("-", 1)
        try:
            return random.randint(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return 3
    try:
        return int(expr)
    except ValueError:
        return 3


# ============================================================
# Pipeline 注册
# ============================================================

PipelineStage = Callable[[ChatContext], Coroutine[Any, Any, Optional[Any]]]

_PIPELINE: List[tuple[str, PipelineStage]] = []


def stage(name: str):
    """装饰器：注册 Pipeline 阶段。"""
    def decorator(func: PipelineStage):
        _PIPELINE.append((name, func))
        return func
    return decorator


# ============================================================
# Pipeline 阶段定义
# ============================================================

@stage("private_whitelist")
async def _stage_private_whitelist(ctx: ChatContext) -> Optional[str]:
    """私聊白名单：仅允许主人（MY_QQ）触发私聊对话。"""
    if not ctx.is_group and ctx.user_id != MY_QQ:
        logger.debug(f"[私聊白名单] 忽略非主人私聊: user={ctx.user_id[:6]}")
        return _SKIP
    return None


@stage("security")
async def _stage_security(ctx: ChatContext) -> Optional[str]:
    """安全扫描：检测 prompt injection 和滥用。"""
    if not ctx.raw_msg:
        return None
    is_safe, reason = scan_input(ctx.raw_msg, ctx.user_id)
    if not is_safe:
        reply = get_blocked_reply(reason)
        await ctx.bot.send(ctx.event, _reply(ctx.event, Message(reply)))
        logger.warning(f"[安全] 拦截消息: user={ctx.user_id[:6]} reason={reason}")
        return _SKIP
    return None


@stage("voice_recognition")
async def _stage_voice(ctx: ChatContext) -> Optional[str]:
    """语音识别：将语音消息转为文字。"""
    if not STT_ENABLED:
        return None
    has_voice = any(seg.type == "record" for seg in ctx.event.get_message())
    if has_voice and not ctx.raw_msg:
        recognized = await recognize_voice(ctx.event)
        if recognized:
            ctx.raw_msg = recognized
            logger.info(f"[STT] 语音识别结果: {ctx.raw_msg[:50]}")
        else:
            logger.info("[STT] 语音识别失败或无内容")
            try:
                await ctx.bot.send(ctx.event, _reply(ctx.event, Message("听不太清楚呢...能打字告诉我吗？")))
            except Exception:
                pass
            return _SKIP
    return None


@stage("rate_limit")
async def _stage_rate_limit(ctx: ChatContext) -> Optional[str]:
    """频率限制。"""
    if not check_rate_limit(ctx.user_id):
        logger.info(f"[限流] 用户 {ctx.user_id} 请求过快，已忽略")
        return _SKIP
    return None


@stage("share_extract")
async def _stage_share(ctx: ChatContext) -> Optional[str]:
    """分享链接提取。"""
    ctx.has_share = await extract_and_cache_shares(ctx.event, ctx.session_id)
    if not ctx.raw_msg and not ctx.has_share:
        return _SKIP
    return None


@stage("share_only_reply")
async def _stage_share_only(ctx: ChatContext) -> Optional[str]:
    """纯分享无文字时的快速回复。"""
    if not ctx.raw_msg and ctx.has_share:
        if not ctx.is_group or ctx.event.is_tome() or random.random() < 0.3:
            recent = get_recent_shares(ctx.session_id)
            last_share = recent[-1] if recent else None
            if last_share and last_share.get("type") == "表情":
                await _handle_emoji_share(ctx, last_share)
            else:
                await _handle_link_share(ctx)
        return _SKIP
    return None


@stage("phone_control")
async def _stage_phone(ctx: ChatContext) -> Optional[str]:
    """手机控制指令处理（仅 MY_QQ 可用）。"""
    if not PHONE_CONTROL_ENABLED:
        return None
    if ctx.user_id != MY_QQ:
        return None
    try:
        # 优先使用 ADB 直连（更稳定）
        from .phone_adb import execute_adb_command, check_device
        if check_device():
            result = execute_adb_command(ctx.raw_msg)
            if result:
                ctx.reply_text = result
                return _SKIP  # 短路：跳过 LLM 等后续阶段

        # ADB 不可用时回退到 ScreenMCP
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
    """群聊消息过滤。"""
    if ctx.is_group:
        is_at_me = ctx.event.is_tome()
        nicknames = ["猫娘", "kitty", "喵喵", "在吗", "bot", "机器人"]
        has_nickname = any(nick in ctx.raw_msg for nick in nicknames)
        should_reply = is_at_me or has_nickname or (random.random() < RANDOM_REPLY_CHANCE)
        if not should_reply:
            return _SKIP
        if is_at_me:
            ctx.raw_msg = re.sub(r'\[CQ:at,qq=\d+\]', '', ctx.raw_msg).strip()
            if not ctx.raw_msg:
                ctx.raw_msg = "在吗"
    return None


@stage("xiaohaihe")
async def _stage_xiaohaihe(ctx: ChatContext) -> Optional[str]:
    """小黑盒特殊处理。"""
    shares_now = get_recent_shares(ctx.session_id)
    latest_share = shares_now[-1] if shares_now else None
    if latest_share and latest_share.get("needs_paste") and latest_share.get("platform") == "小黑盒":
        if ctx.raw_msg and len(ctx.raw_msg) > 100 and not any(kw in ctx.raw_msg for kw in ["讲了什么", "是什么", "怎么看", "这个呢"]):
            latest_share["summary"] = ctx.raw_msg[:2000]
            latest_share["needs_paste"] = False
            latest_share["restricted"] = False
            logger.info(f"[分享] 用户补充了小黑盒正文，长度: {len(ctx.raw_msg)}")
        elif any(kw in ctx.raw_msg for kw in ["讲了什么", "是什么", "内容", "说了什么", "这个呢", "怎么看", "分析一下", "评价"]):
            await ctx.bot.send(ctx.event, _reply(ctx.event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~")))
            return _SKIP
    return None


@stage("affection")
async def _stage_affection(ctx: ChatContext) -> Optional[str]:
    """更新好感度。"""
    await apply_affection_delta(ctx.user_id, ctx.raw_msg)
    return None


@stage("context_analysis")
async def _stage_context(ctx: ChatContext) -> Optional[str]:
    """上下文 + 情绪分析 + 搜索 + 天气（并行执行，节省 1-2 秒）。"""
    # 先获取历史和好感度（后续步骤依赖）
    ctx.recent_memories, ctx.relevant_tags, ctx.affection, ctx.mood, history_for_analysis = \
        await save_and_get_context_with_history(ctx.session_id, ctx.user_id, ctx.raw_msg)

    # 并行执行：情感分析 + 搜索判断 + 天气获取
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
        ctx.reminder_context = await get_pending_reminders_context(ctx.user_id)
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

    # 三个任务并行，取最慢的耗时而非总和
    analysis, search_result, world_ctx = await asyncio.gather(
        _do_analysis(), _do_search(), _do_weather()
    )

    ctx.analysis = analysis
    ctx.search_result = search_result
    ctx.world_context = world_ctx
    ctx.bot_mood_result = await update_bot_emotion(ctx.raw_msg, ctx.analysis.emotion)
    # 功能⑤：根据情绪计算驱动参数
    ctx.emotion_params = get_emotion_params(ctx.analysis.emotion)
    # 功能③：获取用户偏好
    ctx.user_prefs = await get_user_pref_hints(ctx.user_id)

    if ctx.analysis.context.referenced_entity:
        logger.info(f"[指代消解] 检测到指代: {ctx.analysis.context.referenced_entity}")
    return None


@stage("reminder")
async def _stage_reminder(ctx: ChatContext) -> Optional[str]:
    """备忘录/提醒检测（优先级最高，直接回复并返回）。"""
    reminder_intent = is_reminder_request(ctx.raw_msg)
    if reminder_intent == "create":
        reply_text = await create_reminder(ctx.user_id, ctx.session_id, ctx.raw_msg)
        await ctx.bot.send(ctx.event, _reply(ctx.event, Message(reply_text)))
        return _SKIP
    elif reminder_intent == "list":
        reply_text = await list_reminders(ctx.user_id)
        await ctx.bot.send(ctx.event, _reply(ctx.event, Message(reply_text)))
        return _SKIP
    elif reminder_intent == "cancel":
        id_match = re.search(r'(\d+)', ctx.raw_msg)
        if id_match:
            reply_text = await cancel_reminder_by_id(ctx.user_id, int(id_match.group(1)))
        else:
            reply_text = await _generate_reminder_reply("no_reminder")
        await ctx.bot.send(ctx.event, _reply(ctx.event, Message(reply_text)))
        return _SKIP
    return None


@stage("llm_call")
async def _stage_llm(ctx: ChatContext) -> Optional[str]:
    """LLM 主调用（LLM 调用 #2）。"""
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
            await ctx.bot.send(ctx.event, _reply(ctx.event, Message("小黑盒的内容网页端看不了呢...你把正文复制粘贴给我，我帮你分析~")))
            return _SKIP

        length_info = {"target_lines": 4, "style": "专业分析+个性点评"}
        search_ctx = format_search_for_prompt(ctx.search_result) if ctx.search_result else ""
        sys_prompt = _build_system_prompt(
            ctx.affection, ctx.mood, length_info, ctx.relevant_tags, shares_now, ctx.raw_msg,
            context_analysis=ctx.analysis.context, emotion_state=ctx.analysis.emotion,
            search_context=search_ctx, reminder_context=ctx.reminder_context,
            world_context=ctx.world_context, bot_mood=ctx.bot_mood_result,
            user_prefs=ctx.user_prefs,
        )
        sys_prompt += "\n回复风格：专业分析+个性点评。分析部分结构化、有深度，点评部分保持你的猫娘语气。绝对禁止括号动作描写。"
        messages = [{"role": "system", "content": sys_prompt}]
        for mem in ctx.recent_memories[-ANALYSIS_HISTORY_LIMIT:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        messages.append({"role": "user", "content": analysis_prompt})
    else:
        length_info = estimate_reply_length(ctx.raw_msg, ctx.recent_memories, ctx.bot_mood_result)
        # 功能⑤：情绪驱动覆盖回复长度和风格
        length_info["target_lines"] = _parse_target_lines(ctx.emotion_params["target_lines"])
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
            user_prefs=ctx.user_prefs,
        )

        # 问候感知：注入时间上下文和回复约束
        from .database import has_user_message_today
        greeting_type = _detect_greeting_type(ctx.raw_msg)
        if greeting_type == "morning":
            hour = datetime.now().hour
            time_hint = _get_morning_time_hint(hour)
            is_first = not await has_user_message_today(ctx.session_id)
            greet_hint = f"\n【问候感知】用户在跟你说早安。当前时间 {hour}:00。{time_hint}"
            if is_first:
                greet_hint += " 这是用户今天第一条消息，可以自然地问候一下。"
            greet_hint += "\n回复要求：自然、口语化，不要每次都像客服一样'早安~今天也要元气满满哦'。根据时间调整语气。"
            sys_prompt += greet_hint
        elif greeting_type == "night":
            affection_hint = _get_night_affection_hint(ctx.affection)
            sys_prompt += (
                f"\n【道别感知】用户在跟你说晚安/要睡了。{affection_hint}"
                "\n回复要求：短、温暖、不要追问、不要开启新话题。"
                "像关灯一样自然地道别。1句话就够了。"
            )

        messages = [{"role": "system", "content": sys_prompt}]
        history_limit = REPLY_LENGTH_CONFIG["context_depth"] * CHAT_HISTORY_MULTIPLIER
        for mem in ctx.recent_memories[-history_limit:]:
            messages.append({"role": mem["role"], "content": mem["content"]})
        if not messages or messages[-1]["role"] != "user":
            messages.append({"role": "user", "content": ctx.raw_msg})

    # 功能⑤：使用情绪驱动的 temperature 和 max_tokens
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
    """功能④：图片生成阶段。检测触发词并生成图片。"""
    img_config = should_generate_image(ctx.raw_msg)
    if not img_config:
        return None

    # "画"类触发：从用户消息提取描述
    if img_config["id"] == "draw":
        prompt = _extract_draw_prompt(ctx.raw_msg)
    else:
        prompt = img_config["prompt"]

    # 异步生成图片（不阻塞主流程，结果在 post_process 中使用）
    ctx.image_path = await generate_image(prompt)
    if ctx.image_path:
        logger.info(f"[图片] 准备发送: {ctx.image_path}")
    return None


@stage("plugins")
async def _stage_plugins(ctx: ChatContext) -> Optional[str]:
    """功能⑥：执行所有启用的插件。"""
    for plugin in get_enabled_plugins():
        try:
            result = await plugin.on_message(ctx)
            if result is _SKIP:
                return _SKIP
        except Exception as e:
            logger.error(f"[插件] {plugin.meta.name} 执行失败: {e}")
    return None


@stage("post_process")
async def _stage_post(ctx: ChatContext) -> Optional[str]:
    """后处理：保存回复、表情包、发送。"""
    await save_reply(ctx.session_id, ctx.user_id, ctx.raw_msg, ctx.reply_text)

    # 表情包标签处理（功能⑤：情绪驱动表情包概率）
    sticker_chance = ctx.emotion_params.get("sticker_chance", 0.25)
    reply_filtered, sticker_kept = filter_sticker_tag(ctx.reply_text, ctx.session_id, keep_probability=sticker_chance)
    sticker_scene = ""
    if sticker_kept:
        clean_text, sticker_emotion, sticker_scene = parse_sticker_tag(reply_filtered)
    else:
        clean_text = reply_filtered
        # 功能⑤：情绪驱动 fallback 概率（sticker_chance 的 60% 作为 fallback）
        fallback_chance = sticker_chance * 0.6
        sticker_emotion = should_send_sticker_fallback(
            ctx.reply_text,
            ctx.analysis.emotion.dominant if ctx.analysis.emotion.confidence >= 0.4 else None,
            fallback_chance=fallback_chance,
        )

    # 提取链接
    text_for_links, reply_urls = split_reply_and_links(clean_text)
    search_items = extract_shareable_from_search(ctx.search_result) if ctx.search_result else []

    # 发送（根据场景决定是否引用回复）
    should_quote = _should_quote(ctx)
    first_sent = False
    send_as_voice = should_send_voice(ctx.raw_msg, clean_text, ctx.recent_memories)
    if send_as_voice:
        logger.warning(f"[决策] 上下文判断发语音，跳过文字: {clean_text[:30]}...")
        voice_emotion = ctx.analysis.emotion.dominant if ctx.analysis and ctx.analysis.emotion.confidence >= 0.4 else None
        await send_voice(ctx.bot, ctx.event, clean_text, emotion=voice_emotion)
        if reply_urls or search_items:
            rich_msg = build_rich_message("", reply_urls, search_items, show_links=ctx.is_explicit_search)
            if rich_msg:
                await asyncio.sleep(1.5)
                if should_quote:
                    await ctx.bot.send(ctx.event, _reply(ctx.event, rich_msg))
                else:
                    await ctx.bot.send(ctx.event, rich_msg)
                first_sent = True
    else:
        logger.info(f"[决策] 上下文判断发文字: {clean_text[:30]}...")
        if reply_urls or search_items:
            rich_msg = build_rich_message(clean_text, reply_urls, search_items, show_links=ctx.is_explicit_search)
            parts = split_long_reply(str(rich_msg))
            for i, part in enumerate(parts):
                if i > 0:
                    await asyncio.sleep(calc_message_delay(part))
                if not first_sent and should_quote:
                    await ctx.bot.send(ctx.event, _reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True
        else:
            parts = split_long_reply(clean_text)
            for i, part in enumerate(parts):
                if i > 0:
                    await asyncio.sleep(calc_message_delay(part))
                if not first_sent and should_quote:
                    await ctx.bot.send(ctx.event, _reply(ctx.event, Message(part)))
                    first_sent = True
                else:
                    await ctx.bot.send(ctx.event, Message(part))
                    first_sent = True

    # 发送表情包
    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion, sticker_scene)
        if sticker_path:
            await asyncio.sleep(0.8)
            await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 发送: {sticker_emotion}|{sticker_scene} -> {os.path.basename(sticker_path)}")

    # 功能④：发送生成的图片
    if ctx.image_path and os.path.exists(ctx.image_path):
        await asyncio.sleep(1.0)
        await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(ctx.image_path)))
        logger.info(f"[图片] 发送: {os.path.basename(ctx.image_path)}")

    return None


# ============================================================
# 辅助函数（分享快速回复）
# ============================================================

async def _handle_emoji_share(ctx: ChatContext, last_share: dict):
    """处理纯表情分享的快速回复。"""
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
                await asyncio.sleep(random.uniform(0.8, 1.5))
            if i == 0:
                await ctx.bot.send(ctx.event, _reply(ctx.event, Message(part)))
            else:
                await ctx.bot.send(ctx.event, Message(part))
    if sticker_emotion:
        sticker_path = await select_sticker_with_search(sticker_emotion, emoji_scene)
        if sticker_path:
            await asyncio.sleep(0.8)
            await ctx.bot.send(ctx.event, MessageSegment.image(file=Path(sticker_path)))
            logger.info(f"[表情包] 回应表情: {sticker_emotion} -> {os.path.basename(sticker_path)}")
    await save_reply(ctx.session_id, ctx.user_id, f"[表情:{emoji_name}]", send_text)


async def _handle_link_share(ctx: ChatContext):
    """处理纯链接分享的快速回复。"""
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
            await ctx.bot.send(ctx.event, _reply(ctx.event, Message(share_reply)))
        else:
            raise ValueError("回复太短")
    except Exception:
        await ctx.bot.send(ctx.event, _reply(ctx.event, Message("喵？什么东西，让我看看~")))


# ============================================================
# 入口函数（执行 Pipeline）
# ============================================================

async def handle_chat(bot: Bot, event: MessageEvent):
    """主入口：构建上下文并执行 Pipeline。"""
    try:
        ctx = ChatContext(
            bot=bot,
            event=event,
            raw_msg=event.get_message().extract_plain_text().strip(),
            session_id=get_session_id(event),
            user_id=str(event.user_id),
            is_group=isinstance(event, GroupMessageEvent),
        )

        # 执行 Pipeline
        for stage_name, stage_func in _PIPELINE:
            result = await stage_func(ctx)
            if result is _SKIP:
                return

    except Exception as e:
        import traceback
        logger.error(f"[handle_chat] 严重异常: {e}")
        traceback.print_exc()
        try:
            await bot.send(event, _reply(event, Message("呜...脑袋有点乱，让我缓缓...")))
        except Exception:
            pass
