"""Stage: 图片生成 — 检测画图请求并生成图片。"""
from typing import Optional

from nonebot import logger

from ..image_gen import extract_draw_prompt
from ..image_gen import generate_image
from ..image_gen import should_generate_image
from ..pipeline import ChatContext
from ..pipeline import stage


@stage("image_gen")
async def _stage_image_gen(ctx: ChatContext) -> Optional[str]:
    img_config = should_generate_image(ctx.raw_msg)
    if not img_config:
        return None
    if img_config["id"] == "draw":
        prompt = extract_draw_prompt(ctx.raw_msg)
    else:
        prompt = img_config["prompt"]
    ctx.image_path = await generate_image(prompt)
    if ctx.image_path:
        logger.info(f"[图片] 准备发送: {ctx.image_path}")
    return None
