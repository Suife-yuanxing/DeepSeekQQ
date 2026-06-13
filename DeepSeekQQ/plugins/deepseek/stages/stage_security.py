"""Stage: 安全扫描 — 检测输入中的敏感/危险内容并拦截。"""
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..security import get_blocked_reply
from ..security import scan_input


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
