"""语音系统。
- 百度 TTS Token 自动刷新（带过期管理）
- 异步文件 IO
- 可选 ffmpeg -> silk 转码
"""
import asyncio
import base64
import os
import shutil
import urllib.parse
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import aiofiles
import aiohttp
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters.onebot.v11 import MessageSegment

from ._audio_utils import convert_audio_with_ffmpeg
from ._audio_utils import ensure_dir
from ._audio_utils import make_audio_path
from ._audio_utils import safe_remove
from ._audio_utils import schedule_cleanup
from ._audio_utils import validate_file
from ._audio_utils import write_audio_file
from .api import get_http_session
from .config import BAIDU_TTS_AK
from .config import BAIDU_TTS_PER
from .config import BAIDU_TTS_PIT
from .config import BAIDU_TTS_SK
from .config import BAIDU_TTS_SPD
from .config import BAIDU_TTS_VOL
from .config import TTS_ENGINE
from .config import VOICE_CHANCE
from .config import VOICE_DIR
from .config import VOICE_ENABLED_GROUP
from .config import VOICE_ENABLED_PRIVATE
from .config import VOICE_MAX_LENGTH
from .config import VOICE_NAME
from .config import VOICE_TRY_CONVERT
from .config import VOLCANO_APP_ID
from .config import VOLCANO_ACCESS_TOKEN

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
    """将 MP3 转为 QQ 兼容的 silk 格式（腾讯语音编码）。"""
    if not VOICE_TRY_CONVERT or not _has_ffmpeg():
        return None

    silk_path = mp3_path.replace(".mp3", ".silk")
    pcm_path = mp3_path.replace(".mp3", ".pcm")

    # 步骤1: MP3 → PCM (24kHz 单声道)
    if not await convert_audio_with_ffmpeg(mp3_path, pcm_path, sample_rate=24000):
        safe_remove(pcm_path)
        return None

    # 步骤2: PCM → SILK (腾讯 silk_v3_encoder)
    silk_encoder = (
        "/usr/local/bin/silk_v3_encoder"
        if os.path.exists("/usr/local/bin/silk_v3_encoder")
        else (shutil.which("silk_v3_encoder") or shutil.which("silk_encoder"))
    )
    if not silk_encoder:
        logger.warning("[语音] 未找到 silk_v3_encoder，跳过 silk 编码")
        safe_remove(pcm_path)
        return None

    try:
        enc_cmd = [silk_encoder, pcm_path, silk_path, "-tencent"]
        proc = await asyncio.create_subprocess_exec(
            *enc_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()

        if proc.returncode == 0 and validate_file(silk_path, 100):
            logger.info(f"[语音] silk 转码成功: {silk_path}")
            return silk_path
        else:
            logger.warning(f"[语音] silk 编码失败: {stderr.decode()[:200]}")
            safe_remove(silk_path)
            return None
    except Exception as e:
        logger.error(f"[语音] silk 编码异常: {e}")
        safe_remove(silk_path)
        return None
    finally:
        safe_remove(pcm_path)

async def _generate_baidu_voice(text: str, is_singing: bool = False) -> Optional[str]:
    """百度 TTS 引擎。

    Args:
        text: 要合成的文本
        is_singing: 是否歌唱模式（更慢语速、更高音调）
    """
    token = await _get_baidu_token()
    if not token:
        logger.warning("[语音] 百度 Token 获取失败")
        return None

    # 歌唱模式：更慢(3)、稍高音调(6)；正常模式：使用配置值
    spd = 3 if is_singing else BAIDU_TTS_SPD
    pit = 6 if is_singing else BAIDU_TTS_PIT

    tex = urllib.parse.quote(text)
    tts_url = (
        f"https://tsn.baidu.com/text2audio?"
        f"tex={tex}&tok={token}&cuid=deepseek_bot&ctp=1&"
        f"lan=zh&spd={spd}&pit={pit}&vol={BAIDU_TTS_VOL}&per={BAIDU_TTS_PER}&aue=3"
    )
    mp3_path = make_audio_path("deepseek_voice", VOICE_DIR, ".mp3")

    session = await get_http_session()
    try:
        async with session.get(tts_url) as resp:
            data = await resp.read()
            if len(data) < 1000 or data[:2] == b'{"':
                logger.warning(f"[语音] 百度TTS错误/无效: {data[:200]}")
                return None
            if not await write_audio_file(mp3_path, data):
                return None

        if validate_file(mp3_path, 1000):
            mode = "歌唱" if is_singing else "正常"
            logger.info(f"[语音] 百度TTS({mode})生成成功: {mp3_path} ({os.path.getsize(mp3_path)} bytes)")
            return mp3_path
        logger.warning("[语音] 文件过小或不存在")
        return None
    except Exception as e:
        logger.error(f"[语音] 百度TTS失败: {e}")
        safe_remove(mp3_path)
        return None


async def generate_voice_file(text: str, emotion: Optional[str] = None, max_length: int = 0) -> Optional[str]:
    """生成语音文件，返回本地路径。支持引擎降级。

    引擎优先级: volcano > mimo > baidu
    (每个引擎失败后自动 fallback 到下一个)

    Args:
        text: 要合成的文本
        emotion: 情绪标签。特殊值 "singing" 触发歌唱模式（降低语速、调整音调）
        max_length: 文本最大长度限制（0 则使用全局 VOICE_MAX_LENGTH）
    """
    length_limit = max_length if max_length > 0 else VOICE_MAX_LENGTH
    if len(text) > length_limit:
        logger.warning(f"[语音] 文本过长({len(text)}字，限制{length_limit})，跳过语音")
        return None

    # 歌唱模式：调整语速和音调
    is_singing = (emotion == "singing")
    singing_spd = 3  # 更慢，模拟歌唱
    singing_pit = 6  # 稍高音调

    # 火山引擎 TTS（优先级最高）
    if TTS_ENGINE == "volcano" and VOLCANO_APP_ID and VOLCANO_ACCESS_TOKEN:
        from .voice_volcano import generate_volcano_voice
        result = await generate_volcano_voice(text, emotion)
        if result:
            return result
        logger.warning("[语音] 火山 TTS 失败，降级到 MiMo TTS")
        # 继续 fallback

    # MiMo TTS 引擎
    if TTS_ENGINE in ("volcano", "mimo"):  # volcano fallback or mimo direct
        from .voice_mimo import generate_mimo_voice
        result = await generate_mimo_voice(text, emotion)
        if result:
            return result
        if TTS_ENGINE == "mimo":
            logger.warning("[语音] MiMo TTS 失败，降级到百度 TTS")
            return await _generate_baidu_voice(text, is_singing=is_singing)

    # 火山 → MiMo 都失败，降级百度
    if TTS_ENGINE == "volcano":
        logger.warning("[语音] 火山+MiMo 均失败，降级到百度 TTS")
        return await _generate_baidu_voice(text, is_singing=is_singing)

    # 百度 TTS 引擎（默认）
    return await _generate_baidu_voice(text, is_singing=is_singing)

async def send_voice(bot: Bot, event: MessageEvent, text: str, emotion: str = None, max_length: int = 0):
    is_group = isinstance(event, GroupMessageEvent)
    enabled = VOICE_ENABLED_GROUP if is_group else VOICE_ENABLED_PRIVATE
    if not enabled:
        return

    voice_path = await generate_voice_file(text, emotion, max_length=max_length)
    if not voice_path or not validate_file(voice_path, 100):
        logger.info("[语音] 无有效语音文件")
        return

    send_path = voice_path
    try:
        # 尝试 silk 转码（QQ 原生格式，兼容性最好）
        silk_path = await _convert_mp3_to_silk(voice_path)
        if silk_path and validate_file(silk_path, 100):
            send_path = silk_path
            logger.info("[语音] 使用 silk 格式发送")
        else:
            logger.info("[语音] silk 转码不可用，使用 mp3 直发")

        async with aiofiles.open(send_path, "rb") as vf:
            audio_bytes = await vf.read()
            b64 = base64.b64encode(audio_bytes).decode()
        await bot.send(event, MessageSegment.record(file=f"base64://{b64}"))
        logger.info(f"[语音] 发送成功 ({len(audio_bytes)} bytes, {'silk' if send_path.endswith('.silk') else 'mp3'})")
    except Exception as e:
        logger.error(f"[语音] 发送失败: {e}")
    finally:
        schedule_cleanup(voice_path)
        if send_path != voice_path:
            schedule_cleanup(send_path)

def should_send_voice(user_msg: str, reply_text: str, history: List[Dict[str, Any]], voice_mode: bool = False) -> bool:
    import random
    # 语音通话模式下始终发语音
    if voice_mode:
        return True
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


# ============================================================
# 接听/挂断语音
# ============================================================

_GREETINGS = [
    "喂？听到我说话了吗~",
    "嗯嗯，我在呢，说吧~",
    "喵~接通了！今天想聊什么？",
    "嗨嗨~电话接通！有好多话想跟你说呢~",
    "喂喂~是我啦，怎么突然想打电话了？",
]

_FAREWELLS = [
    "那我挂啦，下次再聊哦~",
    "挂了哦，记得想我！",
    "嗯嗯，拜拜~有空再打给我！",
    "好呢~那我挂了，早点休息哦。",
    "拜拜~挂电话啦，mua~",
]


async def send_greeting_voice(bot: Bot, event: MessageEvent):
    """发送接听语音（进入语音通话模式时调用）。"""
    import random
    text = random.choice(_GREETINGS)
    try:
        voice_path = await generate_voice_file(text, emotion="开心", max_length=200)
        if voice_path and validate_file(voice_path, 100):
            await _send_voice_file(bot, event, voice_path)
            logger.info(f"[语音通话] 接听语音发送成功: {text}")
            return
    except Exception as e:
        logger.error(f"[语音通话] 接听语音发送失败: {e}")
    # 语音失败 → 发文字
    try:
        from nonebot.adapters.onebot.v11 import Message
        await bot.send(event, Message(f"[语音通话] {text}"))
    except Exception:
        pass


async def send_farewell_voice(bot: Bot, event: MessageEvent):
    """发送挂断语音（退出语音通话模式时调用）。"""
    import random
    text = random.choice(_FAREWELLS)
    try:
        voice_path = await generate_voice_file(text, emotion="平静", max_length=200)
        if voice_path and validate_file(voice_path, 100):
            await _send_voice_file(bot, event, voice_path)
            logger.info(f"[语音通话] 挂断语音发送成功: {text}")
            return
    except Exception as e:
        logger.error(f"[语音通话] 挂断语音发送失败: {e}")
    # 语音失败 → 发文字
    try:
        from nonebot.adapters.onebot.v11 import Message
        await bot.send(event, Message(f"[语音通话] {text}"))
    except Exception:
        pass


async def _send_voice_file(bot: Bot, event: MessageEvent, voice_path: str):
    """发送语音文件（不传 emotion，因为已经生成好了）。"""
    send_path = voice_path
    try:
        silk_path = await _convert_mp3_to_silk(voice_path)
        if silk_path and validate_file(silk_path, 100):
            send_path = silk_path

        async with aiofiles.open(send_path, "rb") as vf:
            audio_bytes = await vf.read()
            b64 = base64.b64encode(audio_bytes).decode()
        await bot.send(event, MessageSegment.record(file=f"base64://{b64}"))
    finally:
        schedule_cleanup(voice_path)
        if send_path != voice_path:
            schedule_cleanup(send_path)
