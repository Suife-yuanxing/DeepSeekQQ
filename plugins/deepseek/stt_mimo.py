"""MiMo 语音识别（STT）引擎。

API 格式（OpenAI 兼容）:
  POST {base_url}/audio/transcriptions
  Header: api-key: {api_key}
  Body: multipart/form-data (file + model + language)
  Response: {"text": "识别结果"}

支持格式: mp3, wav, amr, pcm, m4a 等
"""
import os
import asyncio
from typing import Optional

import aiohttp
import aiofiles

from nonebot import logger

from .api import get_http_session
from .config import MIMO_STT_API_KEY, MIMO_STT_API_BASE_URL, MIMO_STT_MODEL


async def call_mimo_stt(audio_path: str, language: str = "zh") -> Optional[str]:
    """调用 MiMo STT API 进行语音识别。

    Args:
        audio_path: 本地音频文件路径
        language: 语言代码，默认中文

    Returns:
        识别出的文字，失败返回 None
    """
    if not MIMO_STT_API_KEY:
        logger.warning("[MiMo STT] 未配置 MIMO_STT_API_KEY，跳过")
        return None

    if not os.path.exists(audio_path):
        logger.error(f"[MiMo STT] 文件不存在: {audio_path}")
        return None

    file_size = os.path.getsize(audio_path)
    if file_size < 100:
        logger.warning(f"[MiMo STT] 文件过小: {file_size} bytes")
        return None

    url = f"{MIMO_STT_API_BASE_URL.rstrip('/')}/audio/transcriptions"
    headers = {
        "api-key": MIMO_STT_API_KEY,
    }

    try:
        session = await get_http_session()

        # 构建 multipart form data
        data = aiohttp.FormData()
        async with aiofiles.open(audio_path, "rb") as f:
            file_content = await f.read()
            data.add_field(
                "file",
                file_content,
                filename=os.path.basename(audio_path),
                content_type="audio/wav",
            )
        data.add_field("model", MIMO_STT_MODEL)
        data.add_field("language", language)

        async with session.post(
            url,
            data=data,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"[MiMo STT] API 错误 {resp.status}: {error_text[:200]}")
                return None

            result = await resp.json()
            text = result.get("text", "").strip()
            if text:
                logger.info(f"[MiMo STT] 识别成功: {text[:50]}")
                return text
            else:
                logger.warning(f"[MiMo STT] 响应中无文字: {str(result)[:200]}")
                return None

    except asyncio.TimeoutError:
        logger.error("[MiMo STT] 请求超时(30s)")
        return None
    except Exception as e:
        logger.error(f"[MiMo STT] 异常: {e}")
        return None
