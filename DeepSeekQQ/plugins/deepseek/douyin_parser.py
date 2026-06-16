"""抖音平台分享解析器。

从抖音 SPA 页面的 RENDER_DATA / __NEXT_DATA__ 中提取视频信息。
"""
import json
import re
from typing import Any
from typing import Dict
from typing import Optional
from urllib.parse import unquote


def extract_douyin_render_data(html: str) -> Optional[Dict[str, Any]]:
    """从抖音页面的 RENDER_DATA / __NEXT_DATA__ 中提取视频信息。

    抖音是 SPA 页面，视频数据不在 meta 标签中，而在 <script> 标签的 JSON 里：
    - <script id="RENDER_DATA" type="application/json">URL_ENCODED_JSON</script>
    - <script id="__NEXT_DATA__" type="application/json">JSON</script>

    返回 {desc, nickname, duration, cover_url, comment_count, digg_count, music_title} 或 None。
    """
    render_data = None

    # 方式1: RENDER_DATA（URL编码的JSON）
    match = re.search(
        r'<script[^>]*id="RENDER_DATA"[^>]*type="application/json"[^>]*>(.*?)</script>',
        html, re.DOTALL,
    )
    if match:
        try:
            decoded = unquote(match.group(1).strip())
            render_data = json.loads(decoded)
        except Exception:
            pass

    # 方式2: __NEXT_DATA__（Next.js SSR）
    if not render_data:
        match = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if match:
            try:
                render_data = json.loads(match.group(1))
            except Exception:
                pass

    if not render_data:
        return None

    # 尝试从多种已知 JSON 路径定位 aweme 对象
    aweme = None
    for path in [
        ["aweme", "detail", "aweme"],                      # 经典结构
        ["common", "aweme", "detail", "aweme"],             # 新版通用结构
        ["app", "videoInfoRes", "item_list"],               # 另一变体 (取数组首项)
    ]:
        node = render_data
        for key in path:
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                node = None
                break
        if isinstance(node, dict):
            aweme = node
            break
        elif isinstance(node, list) and node and isinstance(node[0], dict):
            aweme = node[0]
            break

    if not aweme:
        return None

    info: Dict[str, Any] = {}

    if aweme.get("desc"):
        info["desc"] = str(aweme["desc"])

    author = aweme.get("author", {})
    if isinstance(author, dict):
        info["nickname"] = author.get("nickname", "")
        avatar = author.get("avatar_thumb", {})
        if isinstance(avatar, dict):
            urls = avatar.get("url_list", [])
            if urls:
                info["avatar_url"] = str(urls[0])

    video = aweme.get("video", {})
    if isinstance(video, dict):
        info["duration"] = int(video.get("duration", 0) or 0)
        cover = video.get("cover", {}) or video.get("origin_cover", {})
        if isinstance(cover, dict):
            urls = cover.get("url_list", [])
            if urls:
                info["cover_url"] = str(urls[0])

    stats = aweme.get("statistics", {})
    if isinstance(stats, dict):
        info["comment_count"] = int(stats.get("comment_count", 0) or 0)
        info["digg_count"] = int(stats.get("digg_count", 0) or 0)

    music = aweme.get("music", {})
    if isinstance(music, dict) and music.get("title"):
        info["music_title"] = str(music["title"])

    return info if info else None


def parse_douyin(html: str, url: str) -> Optional[Dict[str, str]]:
    """解析抖音分享页面，返回统一格式的分享字典。"""
    from .share_parser import _strip_html

    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "url": url,
    }

    # ── 首选：从 RENDER_DATA / __NEXT_DATA__ 中提取结构化数据 ──
    render_info = extract_douyin_render_data(html)

    if render_info and render_info.get("desc"):
        desc_text = render_info.get("desc", "")
        nickname = render_info.get("nickname", "抖音用户")
        duration = render_info.get("duration", 0)
        cover_url = render_info.get("cover_url", "")
        digg = render_info.get("digg_count", 0)
        comment_cnt = render_info.get("comment_count", 0)
        music_title = render_info.get("music_title", "")

        # 时长格式化
        duration_text = ""
        if duration:
            if duration >= 60:
                duration_text = f"({duration // 60}分{duration % 60}秒)"
            else:
                duration_text = f"({duration}秒)"

        # 互动数据
        stats_parts = []
        if digg:
            stats_parts.append(f"{digg}点赞")
        if comment_cnt:
            stats_parts.append(f"{comment_cnt}评论")
        stats_text = f" [{', '.join(stats_parts)}]" if stats_parts else ""

        summary = f"[抖音视频{duration_text}{stats_text}] {desc_text[:400]}"
        if music_title:
            summary += f" | 🎵{music_title}"

        return {
            **base_fields,
            "title": desc_text[:100] if desc_text else "抖音视频",
            "author": nickname,
            "summary": summary[:800],
            "platform": "douyin",
            "image_url": cover_url,
            "restricted": True,
        }

    # ── 回退：从 meta 标签中提取 ──
    title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
    if not title:
        # 仅在 script 标签内搜索 JSON 字段，避免匹配到无关内容
        scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
        for script in scripts:
            m = re.search(r'"desc"\s*:\s*"([^"]{4,})"', script)
            if m:
                title = m
                break
    if not title:
        title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    title_text = _strip_html(title, "")

    desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
    desc_text = desc.group(1).strip() if desc else ""

    author = None
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for script in scripts:
        m = re.search(r'"nickname"\s*:\s*"([^"]+)"', script)
        if m:
            author = m.group(1).strip()
            break
    if not author:
        author = re.search(r'<meta[^>]*name="author"[^>]*content="([^"]*)"', html)
        author = author.group(1).strip() if author else "抖音用户"

    image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
    image_url = image.group(1) if image else ""

    # 检查是否成功提取到有效内容
    has_valid_title = title_text and title_text not in ("抖音", "抖音视频", "抖音-记录美好生活") and len(title_text) > 2
    has_valid_desc = desc_text and len(desc_text) > 5

    if has_valid_title or has_valid_desc:
        summary_parts = []
        if has_valid_desc and desc_text != title_text:
            summary_parts.append(desc_text[:500])
        summary = " ".join(summary_parts) if summary_parts else title_text
        return {
            **base_fields,
            "title": title_text[:100] if has_valid_title else "抖音视频",
            "author": author,
            "summary": f"[抖音视频] {summary}"[:800],
            "platform": "douyin",
            "image_url": image_url,
            "restricted": True,
        }
    else:
        return {
            **base_fields,
            "title": "抖音视频",
            "author": author,
            "summary": "[抖音视频链接，内容无法读取]",
            "platform": "douyin",
            "image_url": image_url,
            "restricted": True,
            "fetch_failed": True,
        }
