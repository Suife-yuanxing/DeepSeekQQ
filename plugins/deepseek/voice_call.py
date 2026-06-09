"""语音通话模式 — 状态机 + 意图检测。

将已有的 TTS + STT 串联成连续语音对话体验。

触发词:  语音聊天 打电话 通话 语音通话 开语音 接电话
退出词:  挂断 不打了 挂了 挂了吧 结束通话
超时:    5 分钟无消息自动退出
限制:    仅私聊

使用方式:
    from .voice_call import detect_voice_intent, enter_voice_mode, exit_voice_mode, is_in_voice_mode
"""
import asyncio
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import Optional

from nonebot import logger

# ============================================================
# 关键词定义（按长度降序排列，最长匹配优先）
# ============================================================

_ENTER_KEYWORDS = sorted([
    "接电话", "语音通话", "语音聊天", "打电话", "开语音", "通话",
], key=len, reverse=True)

_EXIT_KEYWORDS = sorted([
    "结束通话", "挂了吧", "不打了", "挂断了", "挂断", "挂了",
], key=len, reverse=True)

# ============================================================
# 状态管理
# ============================================================

@dataclass
class VoiceCallState:
    """单个会话的语音通话状态。"""
    active: bool = False
    started_at: float = 0.0
    last_activity: float = 0.0
    timeout_task: Optional[asyncio.Task] = None


# session_id → VoiceCallState
_voice_states: Dict[str, VoiceCallState] = {}

# 超时时间（秒）
TIMEOUT_SECONDS = 300  # 5 分钟


# ============================================================
# 意图检测
# ============================================================

def detect_voice_intent(raw_msg: str) -> Optional[str]:
    """检测消息中是否包含进入/退出语音模式的意图。

    Args:
        raw_msg: 用户原始消息（已 strip）

    Returns:
        "enter" — 进入语音模式
        "exit"  — 退出语音模式
        None    — 无语音意图
    """
    if not raw_msg:
        return None

    msg = raw_msg.strip()

    # 退出优先（用户说"挂了"时先检查退出，避免被"通话"误匹配）
    for kw in _EXIT_KEYWORDS:
        if kw in msg:
            return "exit"

    for kw in _ENTER_KEYWORDS:
        if kw in msg:
            return "enter"

    return None


# ============================================================
# 状态操作
# ============================================================

def is_in_voice_mode(session_id: str) -> bool:
    """检查指定会话是否处于语音通话模式。"""
    state = _voice_states.get(session_id)
    return state is not None and state.active


async def _auto_exit(session_id: str):
    """超时自动退出语音模式。"""
    try:
        await asyncio.sleep(TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        return  # 任务被取消，静默退出
    state = _voice_states.get(session_id)
    if state and state.active:
        logger.info(f"[语音通话] 会话 {session_id[:20]} 超时退出")
        state.active = False
        state.timeout_task = None


def enter_voice_mode(session_id: str) -> VoiceCallState:
    """进入语音通话模式，启动超时定时器。

    如果已在语音模式中，只更新 last_activity。
    """
    state = _voice_states.get(session_id)
    now = time.time()

    if state and state.active:
        # 已在语音模式中，只需更新活跃时间
        state.last_activity = now
        # 重置超时定时器
        _reset_timeout(state, session_id)
        return state

    # 新建状态
    state = VoiceCallState(
        active=True,
        started_at=now,
        last_activity=now,
    )
    _voice_states[session_id] = state
    _reset_timeout(state, session_id)
    logger.info(f"[语音通话] 会话 {session_id[:20]} 进入语音模式")
    return state


def exit_voice_mode(session_id: str) -> bool:
    """退出语音通话模式，取消超时定时器。

    Returns:
        True  — 成功退出
        False — 本来就不在语音模式中
    """
    state = _voice_states.get(session_id)
    if not state or not state.active:
        return False

    state.active = False
    if state.timeout_task:
        state.timeout_task.cancel()
        state.timeout_task = None
    logger.info(f"[语音通话] 会话 {session_id[:20]} 退出语音模式")
    return True


def touch_activity(session_id: str):
    """更新语音模式的活跃时间戳并重置超时。"""
    state = _voice_states.get(session_id)
    if state and state.active:
        state.last_activity = time.time()
        _reset_timeout(state, session_id)


def _reset_timeout(state: VoiceCallState, session_id: str):
    """取消旧超时任务，启动新的超时倒计时。"""
    if state.timeout_task:
        state.timeout_task.cancel()
    from .utils import safe_task
    state.timeout_task = safe_task(_auto_exit(session_id))
