"""语音识别模块（STT）。

功能：
- 检测用户发送的语音消息
- 下载语音文件
- 调用百度语音识别 API 将语音转为文字
- 返回识别结果供主流程使用

使用百度语音识别 REST API：
- 短语音识别：https://vop.baidu.com/server_api
- 支持 pcm/wav/amr 格式，16kHz 采样率
- token 与 TTS 共用
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

from .config import BAIDU_TTS_AK, BAIDU_TTS_SK
from .api import get_http_session

# 百度 STT Token（与 TTS 共用，但 STT 用的是 VOP 接口）
_baidu_token: Optional[str] = None
_baidu_token_expire: float = 0.0


async def _get_baidu_vop_token() -> str:
    """获取百度语音识别 Token（与 TTS 共用 AK/SK）。"""
    global _baidu_token, _baidu_token_expire
    now = time.time()
    if _baidu_token and now < _baidu_token_expire - 3600:
        return _baidu_token

    if not BAIDU_TTS_AK or not BAIDU_TTS_SK:
        return ""

    url = (
        f"https://aip.baidubce.com/oauth/2.0/token?"
        f"grant_type=client_credentials&client_id={BAIDU_TTS_AK}&client_secret={BAIDU_TTS_SK}"
    )
    try:
        session = await get_http_session()
        async with session.get(url) as resp:
            data = await resp.json()
            _baidu_token = data.get("access_token", "")
            expires_in = data.get("expires_in", 2592000)
            _baidu_token_expire = now + expires_in
            return _baidu_token
    except Exception as e:
        logger.error(f"[STT] 获取百度Token失败: {e}")
        return ""


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
        # 转换为 PCM
        pcm_path = await _convert_to_pcm(local_path)
        if not pcm_path:
            logger.warning("[STT] PCM转换失败，尝试直接识别原始格式")
            # 如果已经是 wav 格式，尝试直接用
            if local_path.endswith(".wav"):
                pcm_path = local_path
            else:
                return None

        # 调用百度 STT
        text = await _call_baidu_stt(pcm_path)
        return text
    finally:
        # 异步清理
        asyncio.create_task(_cleanup_files(local_path))
