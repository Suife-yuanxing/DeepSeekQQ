"""Stage: 分享提取 — 从消息中提取分享链接/图片并缓存。"""
import time
from typing import Optional

from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..share_parser import extract_and_cache_shares


@stage("share_extract")
async def _stage_share(ctx: ChatContext) -> Optional[str]:
    ctx.share_cutoff = time.time()  # 记录提取前时间戳，防止旧图片内容泄漏
    ctx.has_share = await extract_and_cache_shares(ctx.event, ctx.session_id)
    if not ctx.raw_msg and not ctx.has_share:
        return _SKIP
    return None
