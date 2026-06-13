"""Stage: 提醒 — 创建/列表/取消提醒的意图识别与处理。"""
import re
from typing import Optional

from nonebot.adapters.onebot.v11 import Message

from ..config import REMINDER_ENABLED
from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..reminder import cancel_reminder_by_id
from ..reminder import create_reminder
from ..reminder import generate_reminder_reply
from ..reminder import is_reminder_request
from ..reminder import list_reminders


@stage("reminder")
async def _stage_reminder(ctx: ChatContext) -> Optional[str]:
    from ..config import REMINDER_ENABLED
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
            reply_text = await generate_reminder_reply("no_reminder")
        await ctx.bot.send(ctx.event, make_reply(ctx.event, Message(reply_text)))
        return _SKIP
    return None
