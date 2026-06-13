"""Stage: 私聊白名单 — 非主人私聊静默忽略。"""
from typing import Optional

from nonebot import logger

from ..config import MY_QQ
from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("private_whitelist")
async def _stage_private_whitelist(ctx: ChatContext) -> Optional[str]:
    """私聊仅限主人（MY_QQ）访问，其余用户静默忽略。"""
    if not ctx.is_group and ctx.user_id != MY_QQ:
        logger.debug(f"[私聊白名单] 忽略非主人私聊: user={ctx.user_id[:6]}")
        return _SKIP
    return None
