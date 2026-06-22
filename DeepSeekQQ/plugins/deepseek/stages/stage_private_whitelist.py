"""Stage: 私聊白名单 — Phase 0.1 已拆，现为直通阶段。

多租户改造：任意 user_id 的私聊消息均可进入 Pipeline。
原逻辑：非 MY_QQ 私聊返回 _SKIP（静默忽略）。
现逻辑：全部放行。仅在 is_debug 时记录统计日志。
"""
import os
from typing import Optional

from nonebot import logger

from ..pipeline import ChatContext
from ..pipeline import stage

# 调试模式：记录被放行的非 owner 私聊
_DEBUG_WHITELIST = os.getenv("DEBUG_PRIVATE_WHITELIST", "").strip() == "1"


@stage("private_whitelist")
async def _stage_private_whitelist(ctx: ChatContext) -> Optional[str]:
    """Phase 0.1: 私聊全部放行，多租户不再限制 MY_QQ。"""
    if _DEBUG_WHITELIST:
        logger.debug(f"[私聊白名单] 放行私聊: user={ctx.user_id[:8]}, is_group={ctx.is_group}")
    return None  # 全部通过
