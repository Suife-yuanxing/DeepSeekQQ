"""B站平台分享解析器。

提取 B站视频页（window.__INITIAL_STATE__）和专栏/Opus 页的内容。
"""
import json
import re
from typing import Any
from typing import Dict
from typing import Optional


def extract_bilibili_video_data(html: str, url: str = "") -> Optional[Dict[str, Any]]:
    """从B站视频页面提取结构化数据。

    优先解析 window.__INITIAL_STATE__ 中的 videoData，
    回退到 og meta 标签。
    """
    info: Dict[str, Any] = {}

    # ── Path 1: window.__INITIAL_STATE__ ──
    pos = html.find("window.__INITIAL_STATE__")
    if pos >= 0:
        eq_pos = html.find("=", pos)
        if eq_pos >= 0:
            brace_pos = html.find("{", eq_pos)
            if brace_pos >= 0:
                depth = 0
                i = brace_pos
                while i < len(html):
                    ch = html[i]
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                        if depth == 0:
                            break
                    i += 1
                if depth == 0:
                    try:
                        data = json.loads(html[brace_pos : i + 1])
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                    vd = data.get("videoData") if isinstance(data, dict) else None
                    if isinstance(vd, dict):
                        info["desc"] = str(vd.get("title", "") or "")
                        info["desc_long"] = str(vd.get("desc", "") or "")
                        owner = vd.get("owner", {})
                        if isinstance(owner, dict):
                            info["nickname"] = str(owner.get("name", "") or "")
                        info["duration"] = int(vd.get("duration", 0) or 0)
                        info["cover_url"] = str(vd.get("pic", "") or "")
                        stat = vd.get("stat", {})
                        if isinstance(stat, dict):
                            info["view_count"] = int(stat.get("view", 0) or 0)
                            info["digg_count"] = int(stat.get("like", 0) or 0)
                            info["comment_count"] = int(stat.get("reply", 0) or 0)
                            info["danmaku_count"] = int(stat.get("danmaku", 0) or 0)
                            info["favorite_count"] = int(stat.get("favorite", 0) or 0)
                        info["pubdate"] = vd.get("pubdate", 0)
                        if info.get("desc"):
                            return info

    # ── Path 2: meta 标签回退 ──
    title_m = re.search(
        r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html
    )
    desc_m = re.search(
        r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html
    )
    image_m = re.search(
        r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html
    )

    if title_m:
        info["desc"] = title_m.group(1).strip()
        if desc_m:
            info["desc_long"] = desc_m.group(1).strip()
        if image_m:
            info["cover_url"] = image_m.group(1).strip()
        return info if info else None

    return None


def parse_bilibili_video(html: str, url: str) -> Optional[Dict[str, str]]:
    """解析 B站视频页面，返回统一格式的分享字典。"""
    from .share_parser import _clean_html, _strip_html

    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "url": url,
    }

    # ── 首选：从 window.__INITIAL_STATE__ 提取结构化数据 ──
    render_info = extract_bilibili_video_data(html, url)

    if render_info and render_info.get("desc"):
        desc_text = render_info.get("desc", "")
        nickname = render_info.get("nickname", "B站UP主")
        duration = render_info.get("duration", 0)
        cover_url = render_info.get("cover_url", "")
        view_count = render_info.get("view_count", 0)
        comment_cnt = render_info.get("comment_count", 0)
        digg_cnt = render_info.get("digg_count", 0)
        danmaku_cnt = render_info.get("danmaku_count", 0)
        desc_long = render_info.get("desc_long", "")

        # 时长格式化
        duration_text = ""
        if duration:
            if duration >= 60:
                duration_text = f"({duration // 60}分{duration % 60}秒)"
            else:
                duration_text = f"({duration}秒)"

        # 互动数据
        stats_parts = []
        if view_count:
            stats_parts.append(f"{view_count}播放")
        if digg_cnt:
            stats_parts.append(f"{digg_cnt}点赞")
        if comment_cnt:
            stats_parts.append(f"{comment_cnt}评论")
        if danmaku_cnt:
            stats_parts.append(f"{danmaku_cnt}弹幕")
        stats_text = f" [{', '.join(stats_parts)}]" if stats_parts else ""

        summary = f"[B站视频{duration_text}{stats_text}] {desc_text[:400]}"
        if desc_long and desc_long != desc_text:
            summary += f" | {desc_long[:300]}"

        return {
            **base_fields,
            "title": desc_text[:100] if desc_text else "B站视频",
            "author": nickname,
            "summary": summary[:800],
            "platform": "bilibili",
            "image_url": cover_url,
            "restricted": True,
        }

    # ── 回退：从 meta 标签提取 ──
    title = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
    if not title:
        title = re.search(r'<title[^>]*>(.*?)</title>', html, re.DOTALL)
    title_text = _strip_html(title, "")

    desc = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
    desc_text = desc.group(1).strip() if desc else ""

    image = re.search(r'<meta[^>]*property="og:image"[^>]*content="([^"]*)"', html)
    image_url = image.group(1) if image else ""

    # 检查是否成功提取到有效内容
    has_valid_title = (
        title_text and len(title_text) > 2
        and "bilibili" not in title_text.lower()
    )
    has_valid_desc = desc_text and len(desc_text) > 5

    if has_valid_title or has_valid_desc:
        summary_parts = []
        if has_valid_desc and desc_text != title_text:
            summary_parts.append(desc_text[:500])
        summary = " ".join(summary_parts) if summary_parts else title_text
        return {
            **base_fields,
            "title": title_text[:100] if has_valid_title else "B站视频",
            "author": "B站UP主",
            "summary": f"[B站视频] {summary}"[:800],
            "platform": "bilibili",
            "image_url": image_url,
            "restricted": True,
        }
    else:
        return {
            **base_fields,
            "title": "B站视频",
            "author": "B站UP主",
            "summary": "[B站视频链接，内容无法读取]",
            "platform": "bilibili",
            "image_url": image_url,
            "restricted": True,
            "fetch_failed": True,
        }


def parse_bilibili_read(html: str, url: str) -> Optional[Dict[str, str]]:
    """解析 B站专栏/Opus 页面，返回统一格式的分享字典。"""
    from .share_parser import _clean_html, _strip_html

    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "url": url,
    }

    title = re.search(r'<h1[^>]*class="[^"]*title[^"]*"[^>]*>(.*?)</h1>', html)
    title = _strip_html(title, "B站专栏")
    author = re.search(r'"name":"([^"]+)"', html)
    author = author.group(1) if author else "未知UP"
    content = re.search(
        r'<div[^>]*id="read-article-holder"[^>]*>(.*?)</div>\s*<div[^>]*class="[^"]*bottom-bar',
        html, re.DOTALL
    )
    if not content:
        content = re.search(
            r'<div[^>]*class="[^"]*opus-module-content[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL
        )
    text = _clean_html(content.group(1)) if content else ""
    return {
        **base_fields,
        "title": title,
        "author": author,
        "summary": text[:1200],
        "platform": "bilibili",
    }
