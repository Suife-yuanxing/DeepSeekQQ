"""音乐功能：点歌、推荐、歌词展示、语音歌唱。

Pipeline stage 入口: handle_music_stage()
"""
import re
import random
import logging
from typing import Optional, Tuple

from nonebot.adapters.onebot.v11 import Message

from .music_api import search_song, get_lyrics, extract_lyrics_snippet, SongInfo
from .music_card import send_music_card
from .handler_helpers import make_reply

logger = logging.getLogger("music")

# ── 意图识别 ──

# 点歌模式（提取歌名）
_SONG_PATTERNS = [
    re.compile(r"(?:播放|放一首?|来一首?|听一首?|唱一首?|来个)\s*(.+)"),
    re.compile(r"我想听\s*(.+)"),
    re.compile(r"点歌[：:]\s*(.+)"),
    re.compile(r"(.+)\s*(?:怎么唱|歌词是什么)"),
]

# 推荐/随机关键词
_RECOMMEND_KEYWORDS = [
    "来首歌", "推荐首歌", "放首歌", "随便来首", "听什么",
    "推荐歌曲", "有什么歌", "来点音乐", "放点音乐",
    "听歌", "来点歌", "随机播放", "放点歌",
]

# 排除词（评价句式，不触发音乐功能）
_EXCLUDE_PATTERNS = [
    re.compile(r".*好不好听.*"),
    re.compile(r".*好听吗.*"),
    re.compile(r".*喜欢吗.*"),
    re.compile(r".*评价一下.*"),
    re.compile(r".*怎么评价.*"),
    re.compile(r".*你觉得.*怎么样.*"),
]


def detect_music_intent(text: str) -> Tuple[str, Optional[str]]:
    """
    检测音乐意图。

    返回: (intent_type, extracted_info)
    - ("search", "歌名")    — 点歌
    - ("recommend", None)   — 推荐/随机
    - ("none", None)        — 非音乐
    """
    text = text.strip()
    if not text:
        return ("none", None)

    # 排除：评价句式不触发
    for pat in _EXCLUDE_PATTERNS:
        if pat.match(text):
            return ("none", None)

    # 推荐/随机
    for kw in _RECOMMEND_KEYWORDS:
        if kw in text:
            return ("recommend", None)

    # 点歌
    for pattern in _SONG_PATTERNS:
        match = pattern.search(text)
        if match:
            song_name = match.group(1).strip()
            # 只删末尾语气词，不删中间的
            song_name = re.sub(r"[的吗呢吧啊哦嗯吧]+$", "", song_name).strip()
            if len(song_name) >= 1:
                return ("search", song_name)

    return ("none", None)


# ── 个性化提示 ──

# 热门歌手（用于判断知名度）
_POPULAR_ARTISTS = {
    "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "邓紫棋", "周深",
    "毛不易", "五月天", "Taylor Swift", "Adele", "Ed Sheeran",
    "BLACKPINK", "BTS", "华晨宇", "李荣浩", "张学友", "王菲",
    "刘德华", "周华健", "蔡依林", "SHE", "TFBOYS", "张杰",
}

# 点歌场景提示模板
_SEARCH_INTROS = [
    "找到了！《{name}》- {artist}~",
    "嗯嗯，《{name}》是吧？{artist}的！",
    "《{name}》~品味不错嘛！",
    "给你找到了~《{name}》！",
    "好嘞！《{name}》- {artist}，马上放~",
    "《{name}》！这首好听！",
]

# 推荐场景 - 热门歌手
_RECOMMEND_POPULAR = [
    "{artist}的歌！这首很经典的~",
    "来听{artist}的《{name}》！",
    "{artist}！这首必听的~",
    "给你推{artist}的歌~《{name}》！",
]

# 推荐场景 - 普通歌手
_RECOMMEND_NORMAL = [
    "来听听《{name}》~ {artist}的",
    "这首不错哦~《{name}》- {artist}",
    "推荐你听《{name}》~",
    "随机到这首了~{artist}的《{name}》",
]

# 推荐场景 - 冷门歌手
_RECOMMEND_INDIE = [
    "这首比较冷门但很好听！《{name}》- {artist}",
    "给你推荐个小众的~《{name}》",
    "这首我私藏的~《{name}》- {artist}",
]

# 时间段前缀
_TIME_PREFIXES = {
    "night": ["这么晚了...来首安静的~", "夜深了，听首歌放松一下~", "睡前来首歌~"],
    "morning": ["早安~来首歌醒醒神！", "早上好！来首歌开启新的一天~", "早~听首歌吧！"],
}


def _build_intro_message(song: SongInfo, intent: str) -> str:
    """根据歌曲信息和意图类型生成个性化提示语。"""
    from datetime import datetime
    hour = datetime.now().hour

    # 时间段
    if 0 <= hour < 6:
        time_period = "night"
    elif 6 <= hour < 10:
        time_period = "morning"
    else:
        time_period = "normal"

    # 选模板
    if intent == "search":
        template = _pick(_SEARCH_INTROS)
    else:
        # 推荐：按歌手知名度选模板
        if any(a in song.artist for a in _POPULAR_ARTISTS):
            template = _pick(_RECOMMEND_POPULAR)
        elif len(song.artist) < 4:
            template = _pick(_RECOMMEND_INDIE)
        else:
            template = _pick(_RECOMMEND_NORMAL)

    # 格式化
    intro = template.format(name=song.name, artist=song.artist)

    # 加时间前缀（30% 概率）
    if time_period != "normal" and random.random() < 0.3:
        prefix = _pick(_TIME_PREFIXES[time_period])
        intro = f"{prefix}\n{intro}"

    return intro


def _pick(pool: list) -> str:
    """从列表中随机选一个。"""
    return random.choice(pool)


# ── 歌词展示 + 语音歌唱 ──

async def _send_lyrics_snippet(bot, event, song: SongInfo) -> None:
    """获取歌词片段，概率发送语音歌唱，否则发文本。"""
    from .config import MUSIC_VOICE_CHANCE

    try:
        lyrics = await get_lyrics(song.id)
        if not lyrics or len(lyrics) < 3:
            return

        snippet = extract_lyrics_snippet(lyrics, max_lines=3)
        if not snippet:
            return

        # 概率发送语音歌唱
        if random.random() < MUSIC_VOICE_CHANCE:
            await _send_voice_sing(bot, event, lyrics, song)
        else:
            await bot.send(event, make_reply(event, Message(snippet)))

    except Exception as e:
        logger.debug(f"[音乐] 歌词片段获取失败（非致命）: {e}")


async def _send_voice_sing(bot, event, lyrics: list, song: SongInfo) -> None:
    """用 TTS 朗读歌词片段，模拟哼唱。"""
    from .voice import send_voice

    # 取 2-3 句歌词
    start = min(4, len(lyrics) - 3)
    lines = lyrics[start:start + 3]
    text = "，".join(lines)

    # 控制长度
    if len(text) > 60:
        text = text[:60]

    try:
        await send_voice(bot, event, text, emotion="happy")
        logger.info(f"[音乐] 语音歌唱发送: {song.name}")
    except Exception as e:
        logger.warning(f"[音乐] 语音歌唱失败: {e}")
        # fallback 为文本
        snippet = extract_lyrics_snippet(lyrics, max_lines=2)
        if snippet:
            await bot.send(event, make_reply(event, Message(snippet)))


# ── 处理函数 ──

async def _handle_recommend(bot, event) -> Optional[str]:
    """处理推荐/随机歌曲请求。"""
    from .config import MUSIC_VOICE_CHANCE

    # 随机选一个搜索关键词
    queries = ["热歌", "流行", "经典", "好听", "华语",
               "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "邓紫棋",
               "Taylor Swift", "周深", "毛不易", "五月天"]
    query = random.choice(queries)
    results = await search_song(query, limit=10)

    if not results:
        await bot.send(event, make_reply(event, Message("诶...暂时想不到推荐什么，你有什么想听的吗？")))
        return "SKIP"

    song = random.choice(results[:5])

    # 个性化提示
    intro = _build_intro_message(song, "recommend")
    await bot.send(event, make_reply(event, Message(intro)))

    # 发音乐卡片
    sent = await send_music_card(bot, event, song)

    # 发歌词/语音
    if sent:
        await _send_lyrics_snippet(bot, event, song)

    return "SKIP"


async def _handle_search(bot, event, song_name: str) -> Optional[str]:
    """处理点歌请求。"""
    results = await search_song(song_name, limit=5)

    if not results:
        await bot.send(event, make_reply(event, Message(f"没找到「{song_name}」诶...换个关键词试试？")))
        return "SKIP"

    song = results[0]

    # 个性化提示
    intro = _build_intro_message(song, "search")
    await bot.send(event, make_reply(event, Message(intro)))

    # 发音乐卡片
    sent = await send_music_card(bot, event, song)

    if not sent:
        # 卡片发送失败，回退为文字
        msg = f"🎵 {song.name} - {song.artist}\n专辑: {song.album}\nhttps://music.163.com/song?id={song.id}"
        await bot.send(event, make_reply(event, Message(msg)))
        return "SKIP"

    # 发歌词/语音
    await _send_lyrics_snippet(bot, event, song)

    return "SKIP"


# ── Pipeline Stage 入口 ──

async def handle_music_stage(ctx) -> Optional[str]:
    """
    Pipeline stage 入口。
    检测意图 → 调用 API → 发送结果 → short-circuit。
    非音乐消息返回 None 继续 pipeline。
    """
    from .config import MUSIC_ENABLED
    if not MUSIC_ENABLED:
        return None

    intent, info = detect_music_intent(ctx.raw_msg)

    if intent == "none":
        return None

    logger.info(f"[音乐] 意图={intent}, info={info}")

    if intent == "recommend":
        return await _handle_recommend(ctx.bot, ctx.event)

    if intent == "search" and info:
        return await _handle_search(ctx.bot, ctx.event, info)

    return None
