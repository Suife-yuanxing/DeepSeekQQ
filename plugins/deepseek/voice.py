"""语音系统。
- 百度 TTS Token 自动刷新（带过期管理）
- 异步文件 IO
- 可选 ffmpeg -> silk 转码
"""
import os
import shutil
import asyncio
import base64
import urllib.parse
from datetime import datetime
from typing import Optional, List, Dict, Any
import aiohttp
import aiofiles

from nonebot.adapters.onebot.v11 import Bot, MessageEvent, GroupMessageEvent, MessageSegment

from .config import (
    BAIDU_TTS_AK, BAIDU_TTS_SK,
    VOICE_ENABLED_PRIVATE, VOICE_ENABLED_GROUP,
    VOICE_CHANCE, VOICE_MAX_LENGTH, VOICE_TRY_CONVERT, VOICE_NAME,
    VOICE_DIR,
    BAIDU_TTS_PER, BAIDU_TTS_SPD, BAIDU_TTS_PIT, BAIDU_TTS_VOL,
    TTS_ENGINE,
)
from .api import get_http_session
from nonebot import logger

BAIDU_TTS_TOKEN: Optional[str] = None
BAIDU_TTS_TOKEN_EXPIRE: float = 0.0

def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None

async def _get_baidu_token() -> str:
    """获取百度 TTS Token，带过期自动刷新。"""
    global BAIDU_TTS_TOKEN, BAIDU_TTS_TOKEN_EXPIRE
    now = datetime.now().timestamp()
    if BAIDU_TTS_TOKEN and now < BAIDU_TTS_TOKEN_EXPIRE - 3600:
        return BAIDU_TTS_TOKEN

    if not BAIDU_TTS_AK or not BAIDU_TTS_SK:
        return ""

    url = (
        f"https://aip.baidubce.com/oauth/2.0/token?"
        f"grant_type=client_credentials&client_id={BAIDU_TTS_AK}&client_secret={BAIDU_TTS_SK}"
    )
    session = await get_http_session()
    try:
        async with session.get(url) as resp:
            data = await resp.json()
            BAIDU_TTS_TOKEN = data.get("access_token", "")
            expires_in = data.get("expires_in", 2592000)
            BAIDU_TTS_TOKEN_EXPIRE = now + expires_in
            return BAIDU_TTS_TOKEN
    except Exception as e:
        logger.error(f"[语音] 获取百度Token失败: {e}")
        return ""

async def _convert_mp3_to_silk(mp3_path: str) -> Optional[str]:
    if not VOICE_TRY_CONVERT or not _has_ffmpeg():
        return None
    silk_path = mp3_path.replace(".mp3", ".silk")
    pcm_path = mp3_path.replace(".mp3", ".pcm")
    try:
        ffmpeg_cmd = [
            "ffmpeg", "-y", "-i", mp3_path,
            "-f", "s16le", "-ar", "24000", "-ac", "1",
            pcm_path
        ]
        proc1 = await asyncio.create_subprocess_exec(
            *ffmpeg_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr1 = await proc1.communicate()
        if proc1.returncode != 0:
            logger.warning(f"[语音] ffmpeg pcm 转换失败: {stderr1.decode()[:200]}")
            return None

        silk_encoder = "/usr/local/bin/silk_v3_encoder" if os.path.exists("/usr/local/bin/silk_v3_encoder") else (shutil.which("silk_v3_encoder") or shutil.which("silk_encoder"))
        if not silk_encoder:
            logger.warning("[语音] 未找到 silk_v3_encoder，跳过 silk 编码")
            try:
                os.remove(pcm_path)
            except Exception:
                pass
            return None

        enc_cmd = [silk_encoder, pcm_path, silk_path, "-tencent"]
        proc2 = await asyncio.create_subprocess_exec(
            *enc_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        _, stderr2 = await proc2.communicate()
        try:
            os.remove(pcm_path)
        except Exception:
            pass

        if proc2.returncode == 0 and os.path.exists(silk_path) and os.path.getsize(silk_path) > 0:
            logger.info(f"[语音] silk 转码成功: {silk_path}")
            return silk_path
        else:
            logger.warning(f"[语音] silk 编码失败: {stderr2.decode()[:200]}")
            return None
    except Exception as e:
        logger.error(f"[语音] 转码异常: {e}")
        for p in [pcm_path, silk_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass
        return None

async def generate_voice_file(text: str, emotion: Optional[str] = None) -> Optional[str]:
    """生成语音文件，返回本地路径。根据 TTS_ENGINE 自动路由。"""
    if len(text) > VOICE_MAX_LENGTH:
        logger.warning(f"[语音] 文本过长({len(text)}字)，跳过语音")
        return None

    # MiMo TTS 引擎
    if TTS_ENGINE == "mimo":
        from .voice_mimo import generate_mimo_voice
        return await generate_mimo_voice(text, emotion)

    # 百度 TTS 引擎（默认）
    token = await _get_baidu_token()
    if not token:
        logger.warning("[语音] 百度 Token 获取失败")
        return None

    tex = urllib.parse.quote(text)
    tts_url = (
        f"https://tsn.baidu.com/text2audio?"
        f"tex={tex}&tok={token}&cuid=deepseek_bot&ctp=1&"
        f"lan=zh&spd={BAIDU_TTS_SPD}&pit={BAIDU_TTS_PIT}&vol={BAIDU_TTS_VOL}&per={BAIDU_TTS_PER}&aue=3"
    )
    mp3_path = f"{VOICE_DIR}/deepseek_voice_{int(datetime.now().timestamp() * 1000)}.mp3"

    session = await get_http_session()
    try:
        async with session.get(tts_url) as resp:
            data = await resp.read()
            if len(data) < 1000 or data[:2] == b'{"':
                logger.warning(f"[语音] 百度TTS错误/无效: {data[:200]}")
                return None
            async with aiofiles.open(mp3_path, "wb") as f:
                await f.write(data)

        if os.path.exists(mp3_path) and os.path.getsize(mp3_path) > 1000:
            logger.info(f"[语音] 百度TTS生成成功: {mp3_path} ({os.path.getsize(mp3_path)} bytes)")
            return mp3_path
        logger.warning("[语音] 文件过小或不存在")
        return None
    except Exception as e:
        logger.error(f"[语音] 百度TTS失败: {e}")
        return None

async def _delayed_cleanup(path: str, delay: int = 300):
    await asyncio.sleep(delay)
    try:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"[语音] 已清理: {path}")
    except Exception as e:
        logger.warning(f"[语音] 清理失败: {e}")

async def send_voice(bot: Bot, event: MessageEvent, text: str, emotion: str = None):
    is_group = isinstance(event, GroupMessageEvent)
    enabled = VOICE_ENABLED_GROUP if is_group else VOICE_ENABLED_PRIVATE
    if not enabled:
        return

    voice_path = await generate_voice_file(text, emotion)
    if not voice_path or not os.path.exists(voice_path):
        logger.info("[语音] 无有效语音文件")
        return

    try:
        async with aiofiles.open(voice_path, "rb") as vf:
            audio_bytes = await vf.read()
            b64 = base64.b64encode(audio_bytes).decode()
        await bot.send(event, MessageSegment.record(file=f"base64://{b64}"))
        logger.info(f"[语音] base64 直发 ({len(audio_bytes)} bytes)")
    except Exception as e:
        logger.error(f"[语音] 发送失败: {e}")
    finally:
        asyncio.create_task(_delayed_cleanup(voice_path))

def should_send_voice(user_msg: str, reply_text: str, history: List[Dict[str, Any]]) -> bool:
    import random
    if "语音测试" in user_msg:
        return True
    if random.random() >= VOICE_CHANCE:
        return False
    if len(reply_text) > VOICE_MAX_LENGTH:
        return False
    if len(user_msg.strip()) <= 3:
        return True
    voice_friendly = ["喵", "哼", "呜", "嘛", "呀", "呢", "啦", "哦", "嗯"]
    if any(w in user_msg for w in voice_friendly):
        return True
    emotional = ["想", "喜欢", "爱", "抱", "亲", "乖", "摸摸"]
    if any(w in user_msg for w in emotional):
        return True
    return random.random() < 0.3
