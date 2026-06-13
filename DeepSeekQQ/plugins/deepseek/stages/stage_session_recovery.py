"""Stage: 会话恢复 — 加载跨会话上下文、工作记忆 scratchpad。"""
from typing import Optional

from nonebot import logger

from ..memory import recover_session_context
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("session_recovery")
async def _stage_session_recovery(ctx: ChatContext) -> Optional[str]:
    ctx.session_recovery = await recover_session_context(ctx.session_id, ctx.user_id)
    if ctx.session_recovery and ctx.session_recovery.get("bot_emotion_memory_hint"):
        ctx.bot_emotion_memory_hint = ctx.session_recovery["bot_emotion_memory_hint"]
    # P0-3: 加载工作记忆（B3: 共享锁防止竞态）
    try:
        from ..db_session import get_session_state, scratchpad_lock
        async with scratchpad_lock:
            state = await get_session_state(ctx.session_id)
            if state and state.get("scratchpad"):
                ctx.scratchpad = state["scratchpad"]
    except Exception as e:
        logger.debug(f"[session] 加载工作记忆失败: {e}")
    return None
