"""网易云音乐 API 封装（直接调用网易云内部接口，无需额外服务）。

API 端点:
- 搜索: POST https://music.163.com/api/search/get
- 歌词: GET  https://music.163.com/api/song/lyric?id=xxx&lv=1
- 详情: GET  https://music.163.com/api/song/detail?ids=[xxx]
"""
import re
import aiohttp
import logging
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass

logger = logging.getLogger("music_api")

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://music.163.com",
}

# ── 数据结构 ──

@dataclass
class SongInfo:
    """歌曲信息"""
    id: int
    name: str
    artist: str          # 歌手名（多个用 / 连接）
    album: str           # 专辑名
    cover_url: str       # 封面图 URL
    duration: int        # 时长（毫秒）
    duration_str: str    # 时长（如 "4:29"）

    @staticmethod
    def from_api(data: dict) -> "SongInfo":
        """从搜索 API 返回的歌曲数据构造"""
        artists = "/".join(a.get("name", "") for a in data.get("artists", []))
        album_data = data.get("album", {})
        cover = album_data.get("picUrl", "")
        # 搜索结果中 cover 在 album.picUrl，详情中在 album.blurPicUrl
        if not cover:
            cover = album_data.get("blurPicUrl", "")
        duration = data.get("duration", 0)
        return SongInfo(
            id=data["id"],
            name=data.get("name", "未知"),
            artist=artists or "未知",
            album=album_data.get("name", ""),
            cover_url=cover,
            duration=duration,
            duration_str=_format_duration(duration),
        )

    @staticmethod
    def from_detail(data: dict) -> "SongInfo":
        """从详情 API 返回的歌曲数据构造"""
        artists = "/".join(a.get("name", "") for a in data.get("artists", []))
        album_data = data.get("album", {})
        cover = album_data.get("picUrl", "") or album_data.get("blurPicUrl", "")
        duration = data.get("duration", 0)
        return SongInfo(
            id=data["id"],
            name=data.get("name", "未知"),
            artist=artists or "未知",
            album=album_data.get("name", ""),
            cover_url=cover,
            duration=duration,
            duration_str=_format_duration(duration),
        )


def _format_duration(ms: int) -> str:
    """毫秒转 mm:ss 格式"""
    if ms <= 0:
        return ""
    total_sec = ms // 1000
    return f"{total_sec // 60}:{total_sec % 60:02d}"


# ── API 调用 ──

async def search_song(keyword: str, limit: int = 5) -> List[SongInfo]:
    """
    搜索歌曲。
    返回匹配结果列表（按相关度排序）。
    """
    url = "https://music.163.com/api/search/get"
    data = {
        "s": keyword,
        "type": 1,       # 1=歌曲
        "limit": limit,
        "offset": 0,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=data, headers=_HEADERS,
                                     timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    logger.warning(f"[音乐] 搜索失败 status={resp.status}")
                    return []
                result = await resp.json()
                songs = result.get("result", {}).get("songs", [])
                return [SongInfo.from_api(s) for s in songs if s.get("id")]
    except Exception as e:
        logger.error(f"[音乐] 搜索异常: {e}")
        return []


async def get_song_detail(song_id: int) -> Optional[SongInfo]:
    """获取单首歌曲详情（含封面）。"""
    url = "https://music.163.com/api/song/detail"
    params = {"ids": f"[{song_id}]"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=_HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                songs = result.get("songs", [])
                if not songs:
                    return None
                return SongInfo.from_detail(songs[0])
    except Exception as e:
        logger.error(f"[音乐] 获取详情异常: {e}")
        return None


async def get_lyrics(song_id: int) -> Optional[List[str]]:
    """
    获取歌词文本（纯歌词，不含时间戳）。
    返回歌词行列表，或 None。
    """
    url = "https://music.163.com/api/song/lyric"
    params = {"id": song_id, "lv": 1, "kv": 1, "tv": -1}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=_HEADERS,
                                    timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                result = await resp.json()
                lrc_text = result.get("lrc", {}).get("lyric", "")
                if not lrc_text:
                    return None
                return _parse_lrc(lrc_text)
    except Exception as e:
        logger.error(f"[音乐] 获取歌词异常: {e}")
        return None


def _parse_lrc(lrc_text: str) -> List[str]:
    """解析 LRC 歌词，提取纯文本行（跳过元数据和空白行）。"""
    lines = []
    meta_keywords = {"作词", "作曲", "编曲", "制作人", "录音", "混音", "和声",
                     "吉他", "贝斯", "鼓", "键盘", "弦乐", "出品", "监制"}
    for line in lrc_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # 去掉时间戳 [mm:ss.xxx]
        text = re.sub(r"\[\d+:\d+\.\d+\]", "", line).strip()
        if not text:
            continue
        # 跳过元数据行
        if any(kw in text for kw in meta_keywords) and "：" in text:
            continue
        if ":" in text and text.split(":")[0].strip() in meta_keywords:
            continue
        lines.append(text)
    return lines


def extract_lyrics_snippet(lyrics: List[str], max_lines: int = 3) -> str:
    """从歌词中提取一段展示（优先取副歌附近的内容）。"""
    if not lyrics:
        return ""
    # 跳过可能的 intro 部分（前几句），取中间偏前的位置
    # 简单策略：跳过前 4 句，取接下来的 max_lines 句
    start = min(4, len(lyrics) - max_lines)
    snippet = lyrics[start:start + max_lines]
    return "\n".join(f"♪ {line}" for line in snippet)


async def search_song_by_lyrics(snippet: str) -> Optional[SongInfo]:
    """
    通过歌词片段搜索歌曲。
    先用片段作为关键词搜索，如果没结果则截取更短的关键词重试。
    """
    # 尝试用完整片段搜索
    results = await search_song(snippet, limit=3)
    if results:
        return results[0]

    # 如果片段太长，截取前面一部分重试
    short = snippet[:15]
    if short != snippet:
        results = await search_song(short, limit=3)
        if results:
            return results[0]

    return None
