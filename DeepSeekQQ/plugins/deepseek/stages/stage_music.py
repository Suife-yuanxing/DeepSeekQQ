"""Stage: 音乐 — 点歌/推荐/歌词意图检测与处理。"""
from typing import Optional

from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("music")
async def _stage_music(ctx: ChatContext) -> Optional[str]:
    """音乐意图检测与处理（点歌、推荐、歌词展示）。"""
    from ..music import handle_music_stage
    result = await handle_music_stage(ctx)
    return _SKIP if result == "SKIP" else None
