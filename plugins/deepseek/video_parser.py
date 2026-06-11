"""视频链接解析 — 基于 bilibili-api-python + yt-dlp 的专业视频信息提取。

替换 share_parser.py 中脆弱的 HTML 抓取方式，使用各平台的官方/社区 SDK：
- B站：bilibili-api-python SDK（直接调用 B 站 API）
- 抖音/YouTube/其他：yt-dlp extract_info（支持 1000+ 平台）
- 所有平台保留 HTML 解析作为最后降级

使用方式：
    from .video_parser import parse_video_url
    info = await parse_video_url(url)  # → VideoInfo dataclass 或 None
"""

import asyncio
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import Optional

from nonebot import logger

# ============================================================
# 数据结构
# ============================================================


@dataclass
class VideoInfo:
    """统一的视频信息结构。"""
    title: str = ""
    description: str = ""
    author: str = ""
    duration: int = 0  # 秒
    cover_url: str = ""
    view_count: int = 0
    like_count: int = 0
    comment_count: int = 0
    danmaku_count: int = 0  # B站专用
    favorite_count: int = 0
    music_title: str = ""  # 抖音专用
    platform: str = "unknown"
    raw_info: Dict[str, Any] = field(default_factory=dict)  # 保留原始数据

    def format_summary(self, max_len: int = 800) -> str:
        """生成用于 LLM prompt 的格式化摘要。"""
        parts = []

        # 平台标签
        platform_labels = {
            "bilibili": "B站视频", "douyin": "抖音视频",
            "youtube": "YouTube视频", "kuaishou": "快手视频",
            "weibo": "微博视频", "xiaohongshu": "小红书视频",
        }
        label = platform_labels.get(self.platform, f"视频({self.platform})")

        # 时长
        duration_text = ""
        if self.duration:
            if self.duration >= 60:
                duration_text = f"({self.duration // 60}分{self.duration % 60}秒)"
            else:
                duration_text = f"({self.duration}秒)"

        # 互动数据
        stats_parts = []
        if self.view_count:
            stats_parts.append(f"{_fmt_count(self.view_count)}播放")
        if self.like_count:
            stats_parts.append(f"{_fmt_count(self.like_count)}点赞")
        if self.comment_count:
            stats_parts.append(f"{_fmt_count(self.comment_count)}评论")
        if self.danmaku_count:
            stats_parts.append(f"{_fmt_count(self.danmaku_count)}弹幕")
        stats_text = f" [{', '.join(stats_parts)}]" if stats_parts else ""

        parts.append(f"[{label}{duration_text}{stats_text}]")

        # 标题
        if self.title:
            parts.append(f" {self.title[:400]}")

        # 描述（不重复标题）
        if self.description and self.description != self.title:
            desc = self.description[:300]
            parts.append(f" | {desc}")

        # 音乐
        if self.music_title:
            parts.append(f" | 🎵{self.music_title}")

        result = "".join(parts)
        return result[:max_len]

    def to_share_dict(self, url: str) -> Dict[str, Any]:
        """转换为 share_parser 兼容的 dict 格式。"""
        title = self.title[:100] if self.title else f"{self.platform}视频"
        summary = self.format_summary()
        return {
            "title": title,
            "author": self.author,
            "summary": summary,
            "platform": self.platform,
            "image_url": self.cover_url,
            "restricted": True,
            "url": url,
            "comments": "",
            "cached": False,
            "needs_paste": False,
            "_video_info": self,  # 保留完整数据
        }


def _fmt_count(n: int) -> str:
    """格式化大数字（1.2万, 99.8万...）"""
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return str(n)


# ============================================================
# B站解析 — bilibili-api-python SDK
# ============================================================

_BILIBILI_PATTERNS = [
    r'(?:https?://)?(?:www\.)?bilibili\.com/video/([a-zA-Z0-9]+)',
    r'(?:https?://)?b23\.tv/([a-zA-Z0-9]+)',
    r'(?:https?://)?(?:www\.)?bilibili\.com/bangumi/play/(?:ss|ep)(\d+)',
]


def _extract_bilibili_id(url: str) -> Optional[str]:
    """从 B站 URL 提取 BV号/AV号/ss号/ep号。

    支持格式：BVxxx, avxxx, b23.tv短链, bangumi ss/ep
    """
    for pattern in _BILIBILI_PATTERNS:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


async def _parse_bilibili(url: str) -> Optional[VideoInfo]:
    """使用 bilibili-api-python SDK 解析 B站视频。

    支持 BV号、av号、b23.tv 短链、番剧 ss/ep 号。
    """
    try:
        from bilibili_api import video, sync
    except ImportError:
        logger.debug("[视频解析] bilibili-api-python 未安装，跳过 B站 SDK")
        return None

    try:
        vid = _extract_bilibili_id(url)
        if not vid:
            return None

        # 同步 API 在线程池中执行
        def _get_info():
            if vid.upper().startswith("BV") or vid.lower().startswith("av"):
                v = video.Video(bvid=vid)
            else:
                try:
                    v = video.Video(aid=int(vid))
                except ValueError:
                    return None
            return sync(v.get_info())

        info = await asyncio.to_thread(_get_info)
        if not info:
            return None

        title = info.get("title", "")
        desc = info.get("desc", "") or ""
        owner = info.get("owner", {})
        stat = info.get("stat", {})
        author = owner.get("name", "") if isinstance(owner, dict) else ""

        return VideoInfo(
            title=title,
            description=desc,
            author=author,
            duration=info.get("duration", 0) or 0,
            cover_url=info.get("pic", ""),
            view_count=stat.get("view", 0) or 0 if isinstance(stat, dict) else 0,
            like_count=stat.get("like", 0) or 0 if isinstance(stat, dict) else 0,
            comment_count=stat.get("reply", 0) or 0 if isinstance(stat, dict) else 0,
            danmaku_count=stat.get("danmaku", 0) or 0 if isinstance(stat, dict) else 0,
            favorite_count=stat.get("favorite", 0) or 0 if isinstance(stat, dict) else 0,
            platform="bilibili",
            raw_info=info,
        )

    except Exception as e:
        logger.warning(f"[视频解析] B站 SDK 解析失败: {type(e).__name__}: {e}")
        return None


# ============================================================
# 通用视频解析 — yt-dlp
# ============================================================

async def _parse_with_ytdlp(url: str) -> Optional[VideoInfo]:
    """使用 yt-dlp 提取视频元数据（不下载视频文件）。

    支持：YouTube、抖音、Twitter、微博、小红书、快手等 1000+ 平台。
    设置短超时防止卡死。
    """
    try:
        import yt_dlp
    except ImportError:
        logger.debug("[视频解析] yt-dlp 未安装，跳过")
        return None

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
        "playlist_items": "1",
        "socket_timeout": 10,  # 10秒超时
        "retries": 1,
    }

    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)

    try:
        info = await asyncio.wait_for(
            asyncio.to_thread(_extract),
            timeout=15.0,  # 总超时15秒
        )
        if not info:
            return None

        # 识别平台
        extractor = info.get("extractor_key", "")
        platform_map = {
            "BiliBili": "bilibili",
            "Douyin": "douyin",
            "Youtube": "youtube",
            "Twitter": "twitter",
            "Weibo": "weibo",
            "XiaoHongShu": "xiaohongshu",
            "Kuaishou": "kuaishou",
            "AcFun": "acfun",
        }
        platform = "unknown"
        for key, plat in platform_map.items():
            if key in extractor:
                platform = plat
                break

        return VideoInfo(
            title=info.get("title", "") or "",
            description=(info.get("description") or "")[:500],
            author=info.get("uploader", "") or info.get("channel", "") or "",
            duration=info.get("duration", 0) or 0,
            cover_url=info.get("thumbnail", "") or "",
            view_count=info.get("view_count", 0) or 0,
            like_count=info.get("like_count", 0) or 0,
            comment_count=info.get("comment_count", 0) or 0,
            platform=platform,
            raw_info=info,
        )

    except asyncio.TimeoutError:
        logger.warning(f"[视频解析] yt-dlp 超时 (15s): {url[:60]}")
        return None
    except Exception as e:
        logger.warning(f"[视频解析] yt-dlp 失败: {type(e).__name__}: {e}")
        return None


# ============================================================
# 主入口
# ============================================================

async def parse_video_url(url: str) -> Optional[VideoInfo]:
    """解析视频 URL，返回统一的 VideoInfo。

    策略（按顺序尝试）：
    1. B站 → bilibili-api-python SDK
    2. 通用 → yt-dlp（15秒超时）
    3. 失败 → 返回 None（调用方降级到原有 HTML 解析）
    """
    if not url:
        return None

    # 1. B站：使用专用 SDK
    if _extract_bilibili_id(url):
        result = await _parse_bilibili(url)
        if result and result.title:
            logger.info(f"[视频解析] B站SDK ✓: {result.title[:40]}... (up:{result.author})")
            return result

    # 2. 通用：yt-dlp
    result = await _parse_with_ytdlp(url)
    if result and result.title:
        logger.info(f"[视频解析] yt-dlp ✓ [{result.platform}]: {result.title[:40]}...")
        return result

    return None
