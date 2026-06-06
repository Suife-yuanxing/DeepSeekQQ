"""语音识别模块（STT）。

功能：
- 检测用户发送的语音消息
- 下载语音文件
- 调用 MiMo STT API（主）或百度语音识别 API（兜底）
- 返回识别结果供主流程使用

引擎优先级：
1. MiMo STT（OpenAI 兼容 whisper）
2. 百度语音识别（兜底）
"""
import os
import asyncio
import json
import time
from typing import Optional
from pathlib import Path

import aiohttp
import aiofiles
from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageEvent, MessageSegment

from .config import BAIDU_TTS_AK, BAIDU_TTS_SK, STT_ENGINE
from .api import get_http_session
from .voice import _get_baidu_token


async def _get_baidu_vop_token() -> str:
    """获取百度语音识别 Token（复用 voice.py 的 Token 管理）。"""
    return await _get_baidu_token()


def _extract_voice_url(event: MessageEvent) -> Optional[str]:
    """从消息事件中提取语音文件URL。"""
    for seg in event.get_message():
        if seg.type == "record":
            # voice 字段包含文件URL或base64
            file_url = seg.data.get("url", "")
            if file_url:
                return file_url
            # 尝试从 file 字段获取
            file_path = seg.data.get("file", "")
            if file_path and file_path.startswith("http"):
                return file_path
    return None


async def _download_voice(url: str) -> Optional[str]:
    """下载语音文件到本地临时目录。"""
    try:
        # 确定保存路径
        voice_dir = "./data/voice"
        os.makedirs(voice_dir, exist_ok=True)
        ext = ".amr"  # QQ语音通常是 amr 格式
        if ".silk" in url:
            ext = ".silk"
        elif ".wav" in url:
            ext = ".wav"
        elif ".mp3" in url:
            ext = ".mp3"

        save_path = os.path.join(voice_dir, f"stt_input_{int(time.time() * 1000)}{ext}")

        session = await get_http_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status != 200:
                logger.warning(f"[STT] 下载语音失败: HTTP {resp.status}")
                return None
            data = await resp.read()
            if len(data) < 100:
                logger.warning(f"[STT] 语音文件太小: {len(data)} bytes")
                return None
            async with aiofiles.open(save_path, "wb") as f:
                await f.write(data)
        logger.info(f"[STT] 下载语音成功: {save_path} ({len(data)} bytes)")
        return save_path
    except Exception as e:
        logger.error(f"[STT] 下载语音异常: {e}")
        return None


async def _convert_to_pcm(input_path: str) -> Optional[str]:
    """将语音文件转换为 PCM 格式（百度 STT 要求）。"""
    pcm_path = input_path.rsplit(".", 1)[0] + ".pcm"
    try:
        # 使用 ffmpeg 转换
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-f", "s16le", "-ar", "16000", "-ac", "1",
            pcm_path
        ]
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr = await proc.communicate()
        if proc.returncode == 0 and os.path.exists(pcm_path) and os.path.getsize(pcm_path) > 100:
            logger.info(f"[STT] PCM转换成功: {pcm_path}")
            return pcm_path
        else:
            logger.warning(f"[STT] PCM转换失败: {stderr.decode()[:200]}")
            return None
    except Exception as e:
        logger.error(f"[STT] PCM转换异常: {e}")
        return None


async def _call_baidu_stt(pcm_path: str) -> Optional[str]:
    """调用百度语音识别 API。"""
    token = await _get_baidu_vop_token()
    if not token:
        logger.warning("[STT] 百度Token获取失败")
        return None

    try:
        async with aiofiles.open(pcm_path, "rb") as f:
            pcm_data = await f.read()

        import base64
        audio_base64 = base64.b64encode(pcm_data).decode("utf-8")

        payload = {
            "format": "pcm",
            "rate": 16000,
            "channel": 1,
            "cuid": "deepseek_bot",
            "token": token,
            "speech": audio_base64,
            "len": len(pcm_data),
        }

        session = await get_http_session()
        async with session.post(
            "https://vop.baidu.com/server_api",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            data = await resp.json()

        err_no = data.get("err_no", -1)
        if err_no == 0:
            result = data.get("result", [])
            if result:
                text = result[0]
                logger.info(f"[STT] 识别成功: {text}")
                return text
        else:
            err_msg = data.get("err_msg", "未知错误")
            logger.warning(f"[STT] 识别失败: err_no={err_no}, msg={err_msg}")
            return None
    except Exception as e:
        logger.error(f"[STT] API调用异常: {e}")
        return None


async def _cleanup_files(*paths):
    """延迟清理临时文件。"""
    await asyncio.sleep(60)
    for p in paths:
        try:
            if p and os.path.exists(p):
                os.remove(p)
        except Exception:
            pass


async def recognize_voice(event: MessageEvent) -> Optional[str]:
    """主入口：从语音消息中识别文字。

    引擎优先级：
    1. MiMo STT（如果配置了 MIMO_STT_API_KEY）
    2. 百度 STT（兜底）

    Returns:
        识别出的文字，或 None（非语音消息/识别失败）
    """
    # 提取语音URL
    voice_url = _extract_voice_url(event)
    if not voice_url:
        return None

    logger.info(f"[STT] 检测到语音消息: {voice_url[:80]}...")

    # 下载语音
    local_path = await _download_voice(voice_url)
    if not local_path:
        return None

    try:
        # 转换为 PCM（百度需要，MiMo 可以直接用原始格式）
        pcm_path = await _convert_to_pcm(local_path)
        if not pcm_path:
            logger.warning("[STT] PCM转换失败，尝试直接识别原始格式")
            if local_path.endswith(".wav"):
                pcm_path = local_path
            else:
                pcm_path = None

        # 引擎 1: MiMo STT（优先）
        if STT_ENGINE == "mimo":
            from .stt_mimo import call_mimo_stt
            # MiMo STT 可以直接处理 amr/wav/mp3 等格式
            text = await call_mimo_stt(local_path)
            if text:
                return text
            logger.warning("[MiMo STT] 识别失败，降级到百度 STT")

        # 引擎 2: 百度 STT（兜底）
        if pcm_path:
            text = await _call_baidu_stt(pcm_path)
            return text
        else:
            logger.warning("[百度 STT] 无 PCM 文件，跳过")
            return None

    finally:
        # 异步清理
        asyncio.create_task(_cleanup_files(local_path))
