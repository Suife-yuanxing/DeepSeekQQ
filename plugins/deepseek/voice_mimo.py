"""MiMo V2.5 TTS 语音合成引擎。

API 格式（OpenAI 兼容）:
  POST {base_url}/chat/completions
  Header: api-key: {api_key}
  Body: {model, messages[user+assistant], audio{format, voice}}
  Response: choices[0].message.audio.data → base64 音频

预置音色: 冰糖(活泼少女) / 茉莉(知性女声) / 苏打(阳光少年) / 白桦(成熟男声)
风格标签: 开心/害羞/傲娇/撒娇/慵懒/难过/生气 等
"""
import os
import binascii
import base64
import asyncio
from datetime import datetime
from typing import Optional

import aiohttp
import aiofiles

from nonebot import logger

from .api import get_http_session
from .config import (
    MIMO_API_KEY, MIMO_API_BASE_URL, MIMO_TTS_VOICE,
    VOICE_MAX_LENGTH, VOICE_DIR,
)

# 默认风格（无情绪时使用）
DEFAULT_STYLE = "温柔甜美，自然可爱"

# 情绪 → MiMo 风格标签映射（基于 bot VA 情绪模型的 dominant 字段）
EMOTION_STYLE_MAP = {
    "开心": "开心活泼，语调上扬",
    "兴奋": "兴奋激动，声音明亮",
    "害羞": "害羞轻声，有点紧张",
    "傲娇": "傲娇，嘴硬心软",
    "平静": "温柔平静，自然放松",
    "无聊": "慵懒，有点犯困",
    "难过": "难过委屈，声音低落",
    "生气": "生气，语气不满",
    "担心": "担心焦虑，语速稍快",
    "害怕": "害怕紧张，声音颤抖",
    "期待": "期待兴奋，充满好奇",
    "感动": "感动温暖，声音柔和",
    "嫌弃": "嫌弃不屑，语气傲慢",
    "撒娇": "撒娇甜美，软软的",
}


async def generate_mimo_voice(text: str, emotion: Optional[str] = None) -> Optional[str]:
    """调用 MiMo TTS 生成语音文件。

    Args:
        text: 要合成的文本
        emotion: 情绪标签（来自 context_analyzer 的 EmotionState.dominant）

    Returns:
        生成的 mp3 文件路径，失败返回 None
    """
    if not MIMO_API_KEY:
        logger.warning("[MiMo TTS] 未配置 MIMO_API_KEY，跳过")
        return None

    if len(text) > VOICE_MAX_LENGTH:
        logger.warning(f"[MiMo TTS] 文本过长({len(text)}字)，跳过")
        return None

    # 确保输出目录存在
    os.makedirs(VOICE_DIR, exist_ok=True)

    # 构建风格指令
    style = EMOTION_STYLE_MAP.get(emotion, DEFAULT_STYLE) if emotion else DEFAULT_STYLE
    style_instruction = f"用{style}的语气说话"

    payload = {
        "model": "mimo-v2.5-tts",
        "messages": [
            {"role": "user", "content": style_instruction},
            {"role": "assistant", "content": text},
        ],
        "audio": {
            "format": "mp3",
            "voice": MIMO_TTS_VOICE,
        },
    }

    url = f"{MIMO_API_BASE_URL.rstrip('/')}/chat/completions"
    headers = {
        "api-key": MIMO_API_KEY,
        "Content-Type": "application/json",
    }

    mp3_path = f"{VOICE_DIR}/mimo_voice_{int(datetime.now().timestamp() * 1000)}.mp3"

    try:
        session = await get_http_session()
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"[MiMo TTS] API 错误 {resp.status}: {error_text[:200]}")
                return None

            data = await resp.json()
            choices = data.get("choices", [])
            if not choices:
                logger.error(f"[MiMo TTS] 响应中无 choices: {str(data)[:200]}")
                return None

            audio_b64 = (
                choices[0]
                .get("message", {})
                .get("audio", {})
                .get("data", "")
            )
            if not audio_b64:
                logger.error(f"[MiMo TTS] 响应中无音频数据: {str(data)[:200]}")
                return None

            try:
                audio_bytes = base64.b64decode(audio_b64)
            except binascii.Error as e:
                logger.error(f"[MiMo TTS] base64 解码失败: {e}")
                return None

            async with aiofiles.open(mp3_path, "wb") as f:
                await f.write(audio_bytes)

            file_size = os.path.getsize(mp3_path)
            if file_size > 1000:
                logger.info(f"[MiMo TTS] 生成成功: {mp3_path} ({file_size} bytes)")
                return mp3_path
            else:
                logger.warning(f"[MiMo TTS] 生成的文件过小: {file_size} bytes")
                # 清理无效文件
                _safe_remove(mp3_path)
                return None

    except asyncio.TimeoutError:
        logger.error("[MiMo TTS] 请求超时(60s)")
        _safe_remove(mp3_path)
        return None
    except Exception as e:
        logger.error(f"[MiMo TTS] 异常: {e}")
        _safe_remove(mp3_path)
        return None


def _safe_remove(path: str):
    """安全删除文件，忽略不存在的情况。"""
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
