"""Stage: 速率限制 — 防止单个用户发送过快。"""
from typing import Optional

from nonebot import logger

from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..utils import check_rate_limit


@stage("rate_limit")
async def _stage_rate_limit(ctx: ChatContext) -> Optional[str]:
    if not check_rate_limit(ctx.user_id):
        logger.info(f"[限流] 用户 {ctx.user_id} 请求过快，已忽略")
        return _SKIP
    return None
