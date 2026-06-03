"""视觉识别模块 - 使用 Qwen-VL 识别表情图片内容。"""
import aiohttp
from typing import Optional
from nonebot import logger

from .config import QWEN_VL_API_KEY, QWEN_VL_MODEL
from .api import get_http_session

QWEN_VL_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

async def recognize_sticker(image_url: str) -> Optional[str]:
    """用 Qwen-VL 识别表情图片，返回情绪描述。"""
    if not QWEN_VL_API_KEY or not image_url:
        return None
    try:
        session = await get_http_session()
        headers = {
            "Authorization": f"Bearer {QWEN_VL_API_KEY}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": QWEN_VL_MODEL,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": "这是一个QQ表情包，请用一个简短的词描述它表达的情绪或动作（如：开心、难过、生气、害羞、无语、卖萌、比心、抱抱等）。只输出这个词，不要其他文字。"}
                ]
            }],
            "max_tokens": 20
        }
        async with session.post(QWEN_VL_URL, headers=headers, json=payload,
                                timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                if content and len(content) <= 10:
                    logger.info(f"[视觉] 识别结果: {content}")
                    return content
            else:
                logger.warning(f"[视觉] API错误: {resp.status}")
    except Exception as e:
        logger.warning(f"[视觉] 识别失败: {e}")
    return None
