"""微博平台分享解析器。

提取微博（weibo.com）页面中的内容：
- 通过 og:meta 标签获取标题/描述
- 通过 render_data / $render_data 提取结构化数据
- 提取正文内容
"""
import json
import re
from typing import Any
from typing import Dict
from typing import Optional


def extract_weibo_render_data(html: str, url: str = "") -> Optional[Dict[str, Any]]:
    """从微博页面提取结构化数据。

    优先解析 window.$render_data 中的 status 数据，
    回退到 og:meta 标签。
    """
    info: Dict[str, Any] = {}

    # ── Path 1: window.$render_data ──
    for marker in ("window.$render_data", "var $render_data"):
        pos = html.find(marker)
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
                        if isinstance(data, dict):
                            status = data.get("status") or {}
                            if isinstance(status, dict):
                                info["text"] = _extract_text(status.get("text", "") or status.get("longText", {}).get("longTextContent", ""))
                                user = status.get("user") or {}
                                if isinstance(user, dict):
                                    info["nickname"] = str(user.get("screen_name", "") or "")
                                info["created_at"] = str(status.get("created_at", "") or "")
                    break

    return info if info else None


def _extract_text(raw: str) -> str:
    """从微博文本中提取纯文本内容。"""
    if not raw:
        return ""
    # 移除 HTML 标签
    text = re.sub(r'<[^>]+>', '', raw)
    # 移除 @ 提及中的链接部分但保留 @nickname
    # 移除 URL
    text = re.sub(r'https?://\S+', '', text)
    # 压缩多余空白
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def parse_weibo(html: str, url: str = "") -> Optional[Dict[str, str]]:
    """解析微博页面，返回统一格式的分享字典。

    Args:
        html: 页面 HTML 内容
        url: 原始分享 URL

    Returns:
        包含 title/author/summary/platform 的字典
    """
    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": False,
        "needs_paste": False,
        "url": url,
    }

    # ── 尝试提取结构化数据 ──
    render_data = extract_weibo_render_data(html, url)
    nickname = ""
    text = ""

    if render_data:
        nickname = render_data.get("nickname", "")
        text = render_data.get("text", "")

    # ── og:meta 标签作为补充 ──
    title_match = re.search(r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html)
    title = _strip_html_match(title_match, nickname or "微博分享")

    desc_match = re.search(r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html)
    meta_desc = ""
    if desc_match:
        meta_desc = desc_match.group(1).strip()

    # 优先使用结构化提取的文本
    summary = text if text else meta_desc

    # ── 微博正文通常在 <div class="WB_text"> 或 <div class="content"> ──
    if not summary or len(summary) < 80:
        # 尝试从正文区域提取
        content_match = re.search(
            r'<div[^>]*class="[^"]*(?:WB_text|detail_wbtext|content)[^"]*"[^>]*>(.*?)</div>',
            html, re.DOTALL,
        )
        if content_match:
            fallback_text = _extract_text(content_match.group(1))
            if len(fallback_text) > len(summary or ""):
                summary = fallback_text

    if not summary:
        return None

    return {
        **base_fields,
        "title": title,
        "author": nickname or "微博用户",
        "summary": summary[:1200],
        "platform": "weibo",
    }


def _strip_html_match(match: Optional[re.Match], fallback: str = "") -> str:
    """从正则匹配中提取文本并清除 HTML 标签。"""
    if not match:
        return fallback
    return re.sub(r'<[^>]+>', '', match.group(1)).strip()
