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

async def get_baidu_token() -> str:
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
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError) as e:
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
    except (OSError, asyncio.TimeoutError) as e:
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
    token = await get_baidu_token()
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
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
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
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"[语音] 发送失败: {e}")
    finally:
        schedule_cleanup(voice_path)
        if send_path != voice_path:
            schedule_cleanup(send_path)

def should_send_voice(user_msg: str, reply_text: str, history: List[Dict[str, Any]],
                      voice_mode: bool = False, affection_score: float = 0.0,
                      bot_mood_dominant: str = "平静",
                      voice_tracker: Optional[Any] = None,
                      user_id: str = "") -> bool:
    """判断是否应该发语音（基于好感度+心情+上下文）。

    Args:
        user_msg: 用户消息文本
        reply_text: bot 回复文本
        history: 对话历史
        voice_mode: 是否在语音通话模式
        affection_score: 好感度分数
        bot_mood_dominant: bot 当前情绪主导词
        voice_tracker: VoiceContextTracker 实例（可选，用于上下文检测）
        user_id: 用户 ID（用于上下文追踪）
    """
    import random
    # 语音通话模式下始终发语音
    if voice_mode:
        return True
    if "语音测试" in user_msg:
        return True

    # === 好感度+心情+上下文综合倍率 ===
    multiplier = 1.0

    # 好感度修正
    if affection_score >= 500:
        multiplier *= 3.0   # 命定之人→经常发语音
    elif affection_score >= 200:
        multiplier *= 2.0   # 重要的人→较多语音
    elif affection_score >= 100:
        multiplier *= 1.5   # 喜欢的人→稍多

    # 心情修正（防冲突：负面情绪抑制语音）
    if bot_mood_dominant in ("开心", "兴奋", "撒娇"):
        multiplier *= 1.5
    elif bot_mood_dominant in ("生气", "难过", "冷淡"):
        multiplier *= 0.3  # 心情不好不发语音
    elif bot_mood_dominant == "犯困":
        multiplier *= 0.5

    # 上下文追踪修正
    if voice_tracker and user_id:
        ctx_mult = voice_tracker.get_boost_multiplier(user_id)
        multiplier *= ctx_mult

    final_chance = VOICE_CHANCE * multiplier

    # === 决策 ===
    if random.random() >= final_chance:
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
# 语音上下文追踪器 — 检测用户提及语音/短期语音冷却
# ============================================================

class VoiceContextTracker:
    """追踪语音相关的上下文状态。

    用于提高语音生成的自然度：
    - 用户提及语音相关词 → 升概率
    - 短时间重复发语音 → 冷却
    - 用户发语音消息 → 双向语音概率提升
    """

    def __init__(self):
        self.last_voice_time: Dict[str, float] = {}       # user_id → 上次发语音时间戳
        self.voice_streak: Dict[str, int] = {}             # user_id → 连续语音次数
        self.voice_mention_times: Dict[str, List[float]] = {}  # user_id → 提及语音的时间戳列表
        self.voice_cooldown_until: Dict[str, float] = {}   # user_id → 冷却结束时间戳
        self._COOLDOWN_SECONDS = 300  # 5分钟冷却

    def user_mentioned_voice(self, user_id: str):
        """用户提及了语音相关词（语音/打电话/发语音/说话/听听/说话啊）。"""
        import time
        now = time.time()
        if user_id not in self.voice_mention_times:
            self.voice_mention_times[user_id] = []
        self.voice_mention_times[user_id].append(now)
        # 只保留最近5分钟的记录
        cutoff = now - 300
        self.voice_mention_times[user_id] = [
            t for t in self.voice_mention_times[user_id] if t > cutoff
        ]
        logger.debug(f"[语音追踪] {user_id[:8]} 提及语音，当前提及数: {len(self.voice_mention_times[user_id])}")

    def voice_sent(self, user_id: str):
        """记录发送了语音，设置冷却和连续追踪。"""
        import time
        now = time.time()
        self.last_voice_time[user_id] = now

        # 连续语音统计
        streak = self.voice_streak.get(user_id, 0) + 1
        self.voice_streak[user_id] = streak

        # 连续3次以上语音 → 强制冷却5分钟
        if streak >= 3:
            self.voice_cooldown_until[user_id] = now + self._COOLDOWN_SECONDS
            self.voice_streak[user_id] = 0  # 重置连续计数
            logger.info(f"[语音追踪] {user_id[:8]} 连续{streak}次语音，触发冷却 {self._COOLDOWN_SECONDS}s")

    def user_sent_voice_message(self, user_id: str):
        """用户发了语音消息（record 段），标记语音互动意愿增强。"""
        import time
        now = time.time()
        if user_id not in self.voice_mention_times:
            self.voice_mention_times[user_id] = []
        # 用户发语音 = 相当于提及了2次语音（权重更高）
        self.voice_mention_times[user_id].extend([now, now])
        # 限制最多保留10条
        cutoff = now - 300
        self.voice_mention_times[user_id] = self.voice_mention_times[user_id][-10:]
        logger.debug(f"[语音追踪] {user_id[:8]} 发了语音消息，语音意愿+2")

    def get_boost_multiplier(self, user_id: str) -> float:
        """返回基于上下文的概率倍率（1.0=无影响）。

        累积规则：
        - 用户每提及一次语音 → +0.3x（最多累积到2.0x）
        - 10分钟内发过语音 → 0.5x（冷却）
        - 强制冷却中 → 0x（不发语音）
        """
        import time
        now = time.time()

        # 强制冷却检查
        cooldown_end = self.voice_cooldown_until.get(user_id, 0)
        if now < cooldown_end:
            logger.debug(f"[语音追踪] {user_id[:8]} 在强制冷却中，剩余{(cooldown_end - now):.0f}s")
            return 0.0

        multiplier = 1.0

        # 用户提及语音 → 升概率
        mention_times = self.voice_mention_times.get(user_id, [])
        # 清理过期记录
        cutoff = now - 300
        mention_times = [t for t in mention_times if t > cutoff]
        self.voice_mention_times[user_id] = mention_times

        if mention_times:
            boost = min(len(mention_times) * 0.3, 1.0)  # 累积但封顶+1.0
            multiplier += boost

        # 短时间冷却：10分钟内发过语音 → 概率减半
        last_time = self.last_voice_time.get(user_id, 0)
        if now - last_time < 600:  # 10分钟
            multiplier *= 0.5

        return max(0.0, multiplier)


# 全局单例
_voice_tracker: Optional[VoiceContextTracker] = None


def get_voice_tracker() -> VoiceContextTracker:
    """获取全局 VoiceContextTracker 单例。"""
    global _voice_tracker
    if _voice_tracker is None:
        _voice_tracker = VoiceContextTracker()
    return _voice_tracker

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
            await send_voice_file(bot, event, voice_path)
            logger.info(f"[语音通话] 接听语音发送成功: {text}")
            return
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as e:
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
            await send_voice_file(bot, event, voice_path)
            logger.info(f"[语音通话] 挂断语音发送成功: {text}")
            return
    except (OSError, aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"[语音通话] 挂断语音发送失败: {e}")
    # 语音失败 → 发文字
    try:
        from nonebot.adapters.onebot.v11 import Message
        await bot.send(event, Message(f"[语音通话] {text}"))
    except Exception:
        pass


async def send_voice_file(bot: Bot, event: MessageEvent, voice_path: str):
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
