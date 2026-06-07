"""音乐功能：点歌、推荐、歌词展示。

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
    re.compile(r"(?:播放|放|来|听|唱)(?:一首|首|个|一下)?\s*(.+)"),
    re.compile(r"我想听\s*(.+)"),
    re.compile(r"点歌[：:]\s*(.+)"),
    re.compile(r"(.+)\s*(?:怎么唱|歌词是什么|歌词)"),
]

# 推荐/随机关键词
_RECOMMEND_KEYWORDS = [
    "来首歌", "推荐首歌", "放首歌", "随便来首", "听什么",
    "推荐歌曲", "有什么歌", "来点音乐", "放点音乐",
    "听歌", "来点歌", "随机播放",
]

# 不应触发音乐功能的排除词
_EXCLUDE_KEYWORDS = [
    "怎么", "为什么", "是什么", "哪个", "哪些",
    "好不好听", "好听吗", "喜欢吗", "评价",
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

    # 排除：如果是问句或评价，不触发
    if any(kw in text for kw in _EXCLUDE_KEYWORDS):
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
            # 清理歌名中的多余内容
            song_name = re.sub(r"[的吗呢吧啊哦嗯吧]", "", song_name).strip()
            if len(song_name) >= 1:
                return ("search", song_name)

    return ("none", None)


# ── 推荐歌曲 ──

# 预设推荐关键词（用于搜索热歌）
_RECOMMEND_QUERIES = [
    "热歌", "流行", "经典", "好听", "华语",
    "周杰伦", "林俊杰", "陈奕迅", "薛之谦", "邓紫棋",
    "Taylor Swift", "周深", "毛不易", "五月天",
]

# 推荐时的开场白
_RECOMMEND_INTROS = [
    "给你推荐一首~",
    "来听听这个！",
    "这首不错哦~",
    "随机到这首了~",
    "我觉得你可能会喜欢这个！",
    "突然想到这首歌~",
]


async def _handle_recommend(bot, event) -> Optional[str]:
    """处理推荐/随机歌曲请求。"""
    # 随机选一个搜索关键词
    query = random.choice(_RECOMMEND_QUERIES)
    results = await search_song(query, limit=10)

    if not results:
        await bot.send(event, make_reply(event, Message("诶...暂时想不到推荐什么，你有什么想听的吗？")))
        return "SKIP"

    # 随机选一首
    song = random.choice(results[:5])

    # 发推荐语
    intro = random.choice(_RECOMMEND_INTROS)
    await bot.send(event, make_reply(event, Message(intro)))

    # 发音乐卡片
    sent = await send_music_card(bot, event, song)

    # 发歌词片段
    if sent:
        await _send_lyrics_snippet(bot, event, song)

    return "SKIP"


async def _handle_search(bot, event, song_name: str) -> Optional[str]:
    """处理点歌请求。"""
    results = await search_song(song_name, limit=5)

    if not results:
        await bot.send(event, make_reply(event, Message(f"没找到「{song_name}」诶...换个关键词试试？")))
        return "SKIP"

    # 取第一个结果
    song = results[0]

    # 发音乐卡片
    sent = await send_music_card(bot, event, song)

    if not sent:
        # 卡片发送失败，回退为文字
        msg = f"🎵 {song.name} - {song.artist}\n专辑: {song.album}\nhttps://music.163.com/song?id={song.id}"
        await bot.send(event, make_reply(event, Message(msg)))
        return "SKIP"

    # 发歌词片段
    await _send_lyrics_snippet(bot, event, song)

    return "SKIP"


async def _send_lyrics_snippet(bot, event, song: SongInfo) -> None:
    """获取并发送歌词片段（2-3句）。"""
    try:
        lyrics = await get_lyrics(song.id)
        if lyrics and len(lyrics) >= 3:
            snippet = extract_lyrics_snippet(lyrics, max_lines=3)
            if snippet:
                await bot.send(event, make_reply(event, Message(snippet)))
    except Exception as e:
        logger.debug(f"[音乐] 歌词片段获取失败（非致命）: {e}")


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
