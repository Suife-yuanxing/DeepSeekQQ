"""Pipeline 基础设施 — ChatContext、stage 注册器、主入口。

拆分自 handler.py（P2-7），提供所有 stage 共享的核心类型和函数。
"""
import time
from dataclasses import dataclass
from dataclasses import field
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


# ============================================================
# 短路标记（从 constants 导入，消除 agent_base ↔ pipeline 循环引用）
# ============================================================

from .constants import _SKIP  # noqa: F401 — 向后兼容重导出


# ============================================================
# Pipeline 共享上下文
# ============================================================

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
    share_cutoff: float = 0.0  # 时间戳，用于过滤当前消息的分享（防止旧图片内容泄漏）
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
    # 社交信息流引擎
    scroll_hint: str = ""         # feed引用提示（"刚刷到的..."）
    should_inject_feed: bool = False  # 是否应该注入feed提示到prompt
    feed_injected: bool = False   # 本轮是否已经注入过feed
    # 群聊热度状态机
    heat_state: str = ""          # 热度状态描述
    # 个性化深化
    nickname_hint: str = ""
    interest_hint: str = ""
    growth_hint: str = ""
    catchphrase_hint: str = ""
    # 真人化 P3-4.4：口头禅双向影响
    catchphrase_influence_hint: str = ""
    # 真人化优化
    complexity: str = "normal"        # simple / normal / complex
    # 已读不回感知
    reply_gap_hint: str = ""
    # 跨会话情绪记忆
    bot_emotion_memory_hint: str = ""
    # 群聊热度状态机
    group_heat_state: str = ""           # dormant / idle / active
    group_heat_description: str = ""     # 群活跃度自然语言描述
    # P0-3: 工作记忆 — 跨轮次对话状态
    scratchpad: str = ""
    # 对话疲劳感知
    fatigue_level: int = 0
    fatigue_hint: str = ""
    # 用户画像摘要（念念对用户的认识总结）
    user_profile_summary: str = ""
    # 当前活动状态（activity_sim模块）
    activity_hint: str = ""
    can_interrupt: bool = True  # 真人化Q6：当前活动是否可中断回复
    # 人设演化提示
    personality_drift_hints: list = field(default_factory=list)
    # 价值体系：bot的立场/三观
    value_hints: list = field(default_factory=list)
    past_opinions: list = field(default_factory=list)
    past_opinions_hint: str = ""
    # 结构化天气数据（供行为引擎使用，避免 regex 解析格式化字符串）
    _weather_info: Any = None
    # 场景路由结果（供 prompt_templates 使用）
    scenes: list = field(default_factory=list)
    # 语音通话模式
    voice_mode: bool = False
    # 手机命令直接处理标记（跳过 LLM 调用）
    skip_llm: bool = False
    # B2: 表情包分享上下文（走 normal pipeline 时使用）
    emoji_share_name: str = ""
    emoji_share_emotion: str = ""


# ============================================================
# Pipeline 注册
# ============================================================

PipelineStage = Callable[[ChatContext], Coroutine[Any, Any, Optional[Any]]]
_PIPELINE: List[tuple[str, PipelineStage]] = []


def stage(name: str):
    """装饰器：注册一个 Pipeline 阶段。

    被装饰的函数必须接受 ChatContext 并返回 None（继续）或 _SKIP（短路）。
    阶段按装饰顺序执行。
    """
    def decorator(func: PipelineStage):
        _PIPELINE.append((name, func))
        return func
    return decorator


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
    from .handler_helpers import classify_message_complexity
    from .handler_helpers import make_reply
    from .performance_monitor import StageTimer
    from .performance_monitor import track_response
    from .utils import get_session_id
    from .utils import safe_task

    start_time = time.time()

    # === 立刻发"正在输入"状态（不等延迟）===
    safe_task(_set_typing_status(bot, event, True))

    try:
        # 预检测消息中的图片和语音（用于消息分级）
        _msg_segments = event.get_message()
        _has_image = any(seg.type == "image" and seg.data.get("sub_type", 0) != 1 for seg in _msg_segments)
        _has_voice = any(seg.type == "record" for seg in _msg_segments)
        _raw_msg = _msg_segments.extract_plain_text().strip()

        # H-9: 截断超长用户消息，防止消耗过多 Token
        from .config import MAX_USER_MSG_CHARS
        _truncated_msg = _raw_msg[:MAX_USER_MSG_CHARS] if len(_raw_msg) > MAX_USER_MSG_CHARS else _raw_msg

        ctx = ChatContext(
            bot=bot,
            event=event,
            raw_msg=_truncated_msg,
            session_id=get_session_id(event),
            user_id=str(event.user_id),
            is_group=isinstance(event, GroupMessageEvent),
            complexity=classify_message_complexity(_raw_msg, _has_image, _has_voice),
        )

        # A3: AgentRouter 前置过滤（3 agent: security/music/phone_direct）
        # 异常时自动回退到完整 Pipeline
        try:
            from .agents import router as _agent_router
            if await _agent_router.dispatch(ctx):
                return
        except Exception:
            logger.exception("[AgentRouter] dispatch 异常，回退到完整 Pipeline")

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
