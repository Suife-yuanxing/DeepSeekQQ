"""Stage: 小黑盒 — 小黑盒分享的正文粘贴提示。"""
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message

from ..handler_helpers import make_reply
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..share_parser import get_recent_shares


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
