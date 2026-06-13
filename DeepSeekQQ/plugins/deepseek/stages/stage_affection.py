"""Stage: 好感度 — 应用好感度变化 + 检测退订/订阅意图。"""
from typing import Optional

from nonebot import logger

from ..memory import apply_affection_delta
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("affection")
async def _stage_affection(ctx: ChatContext) -> Optional[str]:
    await apply_affection_delta(ctx.user_id, ctx.raw_msg)
    # P1-12: 检测退订/订阅意图
    try:
        from ..proactive_gate import process_opt_message
        opt_result = await process_opt_message(ctx.user_id, ctx.raw_msg)
        if opt_result == "opted_out":
            logger.info(f"[主动消息] 用户 {ctx.user_id[:6]} 已退订")
        elif opt_result == "opted_in":
            logger.info(f"[主动消息] 用户 {ctx.user_id[:6]} 已恢复订阅")
    except Exception as e:
        logger.debug(f"[主动消息] 退订检测异常: {e}")
    return None
