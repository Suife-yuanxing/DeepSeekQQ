"""火山引擎 TTS 语音合成引擎。

API 格式:
  POST https://openspeech.bytedance.com/api/v1/tts
  Header: Authorization: Bearer; {access_token}  （注意分号分隔！）
  Body: {app, user, audio, request}
  Response: {"code":3000,"message":"Success","data":"base64_encoded_mp3"}

预置音色（BV*_streaming 系列）:
  BV001 - 女声1（通用女声）        BV002 - 女声2（甜美）
  BV004 - 女声4（自然）             BV405 - 情感女声（开心）
  BV406 - 情感女声（温柔）          BV407 - 情感女声（撒娇）
  BV408 - 情感女声（傲娇）          BV700 - 女声（知性）
  BV701 - 女声（活泼）
"""
import asyncio
import base64
import binascii
import json
import os
import uuid
from typing import Optional

import aiohttp
from nonebot import logger

from ._audio_utils import ensure_dir
from ._audio_utils import make_audio_path
from ._audio_utils import safe_remove
from ._audio_utils import validate_file
from ._audio_utils import write_audio_file
from .api import get_http_session
from .config import VOLCANO_APP_ID
from .config import VOLCANO_ACCESS_TOKEN
from .config import VOLCANO_VOICE_TYPE
from .config import VOICE_DIR

# 火山引擎 TTS API 端点
VOLCANO_TTS_URL = "https://openspeech.bytedance.com/api/v1/tts"

# 情绪 → 音色映射（猫娘人设联动 — 娇喘女声大模型音色为主调）
EMOTION_VOICE_MAP = {
    "开心": "zh_female_jiaochuannv_uranus_bigtts",
    "兴奋": "zh_female_jiaochuannv_uranus_bigtts",
    "害羞": "zh_female_jiaochuannv_uranus_bigtts",
    "傲娇": "zh_female_jiaochuannv_uranus_bigtts",
    "平静": "zh_female_jiaochuannv_uranus_bigtts",
    "无聊": "zh_female_jiaochuannv_uranus_bigtts",
    "难过": "zh_female_jiaochuannv_uranus_bigtts",
    "生气": "zh_female_jiaochuannv_uranus_bigtts",
    "担心": "zh_female_jiaochuannv_uranus_bigtts",
    "害怕": "zh_female_jiaochuannv_uranus_bigtts",
    "期待": "zh_female_jiaochuannv_uranus_bigtts",
    "感动": "zh_female_jiaochuannv_uranus_bigtts",
    "嫌弃": "zh_female_jiaochuannv_uranus_bigtts",
    "撒娇": "zh_female_jiaochuannv_uranus_bigtts",
    "爱": "zh_female_jiaochuannv_uranus_bigtts",
    "温柔": "zh_female_jiaochuannv_uranus_bigtts",
}
DEFAULT_VOICE = "zh_female_jiaochuannv_uranus_bigtts"  # 娇喘女声-大模型


async def generate_volcano_voice(
    text: str,
    emotion: Optional[str] = None,
    voice_type: Optional[str] = None,
) -> Optional[str]:
    """调用火山引擎 TTS 生成语音文件。

    Args:
        text: 要合成的文本
        emotion: 情绪标签（来自 context_analyzer 的 EmotionState.dominant）
        voice_type: 音色覆盖（不传则根据情绪自动选择）

    Returns:
        生成的 mp3 文件路径，失败返回 None
    """
    app_id = VOLCANO_APP_ID
    access_token = VOLCANO_ACCESS_TOKEN

    if not app_id or not access_token:
        logger.warning("[火山TTS] 未配置 VOLCANO_APP_ID 或 VOLCANO_ACCESS_TOKEN，跳过")
        return None

    # 确保输出目录存在
    ensure_dir(VOICE_DIR)

    # 选择音色：手动覆盖 > 情绪映射 > 全局默认 > 硬编码默认
    if not voice_type:
        voice_type = EMOTION_VOICE_MAP.get(emotion) if emotion else None
    if not voice_type:
        voice_type = VOLCANO_VOICE_TYPE or DEFAULT_VOICE

    # 构建请求
    payload = {
        "app": {
            "appid": app_id,
            "token": access_token,
            "cluster": "volcano_tts",
        },
        "user": {
            "uid": str(uuid.uuid4()),
        },
        "audio": {
            "encoding": "mp3",
            "voice_type": voice_type,
            "rate": 24000,
        },
        "request": {
            "reqid": str(uuid.uuid4()),
            "text": text,
            "text_type": "plain",
            "operation": "query",
        },
    }

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer; {access_token}",
    }

    mp3_path = make_audio_path("volcano_voice", VOICE_DIR, ".mp3")

    try:
        session = await get_http_session()
        async with session.post(
            VOLCANO_TTS_URL, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                error_text = await resp.text()
                logger.error(f"[火山TTS] API 错误 {resp.status}: {error_text[:200]}")
                return None

            data = await resp.json()

        # 检查业务状态码（3000 = 成功）
        code = data.get("code")
        if code != 3000:
            message = data.get("message", "未知错误")
            logger.error(f"[火山TTS] 业务错误 code={code}: {message}")
            return None

        # 提取 base64 音频数据
        audio_b64 = data.get("data", "")
        if not audio_b64:
            logger.error("[火山TTS] 响应中无音频数据")
            return None

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except binascii.Error as e:
            logger.error(f"[火山TTS] base64 解码失败: {e}")
            return None

        if len(audio_bytes) < 500:
            logger.warning(f"[火山TTS] 音频数据过小: {len(audio_bytes)} bytes")
            return None

        if not await write_audio_file(mp3_path, audio_bytes):
            return None

        if validate_file(mp3_path, 500):
            logger.info(
                f"[火山TTS] 生成成功: {mp3_path} "
                f"({os.path.getsize(mp3_path)} bytes, voice={voice_type}, emotion={emotion or '默认'})"
            )
            return mp3_path
        else:
            logger.warning(f"[火山TTS] 生成的文件过小: {os.path.getsize(mp3_path)} bytes")
            safe_remove(mp3_path)
            return None

    except asyncio.TimeoutError:
        logger.error("[火山TTS] 请求超时(30s)")
        safe_remove(mp3_path)
        return None
    except aiohttp.ClientError as e:
        logger.error(f"[火山TTS] 网络异常: {e}")
        safe_remove(mp3_path)
        return None
    except Exception as e:
        logger.error(f"[火山TTS] 异常: {e}")
        safe_remove(mp3_path)
        return None
