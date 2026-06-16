"""小红书平台分享解析器。

提取小红书（xiaohongshu.com / xhslink.com）页面中的内容：
- 通过 window.__INITIAL_STATE__ 提取结构化数据
- 回退到 og:meta 标签
- 处理小红书的限制策略（部分内容需要登录查看）
"""
import json
import re
from typing import Any
from typing import Dict
from typing import Optional


def extract_xiaohongshu_initial_state(html: str, url: str = "") -> Optional[Dict[str, Any]]:
    """从小红书页面提取 window.__INITIAL_STATE__ 结构化数据。"""
    info: Dict[str, Any] = {}

    pos = html.find("window.__INITIAL_STATE__")
    if pos < 0:
        return None

    # 找到 JSON 字符串的起止位置（可能跨行，需处理转义）
    # __INITIAL_STATE__ = JSON.parse("...") 或 __INITIAL_STATE__ = {...}
    eq_pos = html.find("=", pos)
    if eq_pos < 0:
        return None

    # 跳过 JSON.parse( 前缀
    remaining = html[eq_pos + 1:].strip()
    if remaining.startswith("JSON.parse("):
        json_start = remaining.find('"', 11)
        if json_start < 0:
            return None
        json_start += 1
        # 找到对应的结束引号（处理转义）
        i = json_start
        while i < len(remaining):
            if remaining[i] == '\\':
                i += 2
                continue
            if remaining[i] == '"':
                json_str = remaining[json_start:i]
                try:
                    decoded = json.loads(json_str.replace('\\"', '"').replace('\\\\', '\\'))
                except (json.JSONDecodeError, TypeError):
                    decoded = None
                if isinstance(decoded, dict):
                    _extract_note_from_state(decoded, info)
                return info if info else None
            i += 1
        return None

    # 直接 JSON 对象形式
    remaining = html[eq_pos + 1:].strip()
    if remaining.startswith("{"):
        depth = 0
        i = 0
        while i < len(remaining):
            ch = remaining[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        data = json.loads(remaining[:i + 1])
                    except (json.JSONDecodeError, TypeError):
                        data = {}
                    if isinstance(data, dict):
                        _extract_note_from_state(data, info)
                    return info if info else None
            i += 1
    return None


def _extract_note_from_state(data: dict, info: dict):
    """从 __INITIAL_STATE__ 中递归提取笔记/帖子数据。"""
    # 常见路径：note.noteDetailMap -> {noteId: {note: {...}}}
    note = data.get("note")
    if isinstance(note, dict):
        detail_map = note.get("noteDetailMap") or {}
        if isinstance(detail_map, dict):
            for note_id, note_data in detail_map.items():
                if isinstance(note_data, dict):
                    inner_note = note_data.get("note") or {}
                    if isinstance(inner_note, dict):
                        info["title"] = str(inner_note.get("title", "") or "")
                        info["desc"] = str(inner_note.get("desc", "") or "")
                        author = inner_note.get("user") or {}
                        if isinstance(author, dict):
                            info["nickname"] = str(author.get("nickname", "") or "")
                        return

    # 回退：直接查找可能的昵称/标题
    if not info.get("title"):
        for key in ("title", "postTitle", "articleTitle"):
            val = _deep_get(data, key)
            if val:
                info["title"] = str(val)
                break
    if not info.get("nickname"):
        for key in ("nickname", "authorName", "username"):
            val = _deep_get(data, key)
            if val:
                info["nickname"] = str(val)
                break
    if not info.get("desc"):
        for key in ("desc", "description", "content", "text"):
            val = _deep_get(data, key)
            if val:
                info["desc"] = str(val)
                break


def _deep_get(obj: Any, key: str, max_depth: int = 5) -> Optional[Any]:
    """在嵌套字典中递归查找键值。"""
    if max_depth <= 0 or not isinstance(obj, dict):
        return None
    if key in obj and obj[key]:
        return obj[key]
    for v in obj.values():
        result = _deep_get(v, key, max_depth - 1)
        if result:
            return result
    return None


def parse_xiaohongshu(html: str, url: str = "") -> Optional[Dict[str, str]]:
    """解析小红书页面，返回统一格式的分享字典。

    Args:
        html: 页面 HTML 内容
        url: 原始分享 URL

    Returns:
        包含 title/author/summary/platform 的字典
    """
    base_fields = {
        "comments": "",
        "cached": False,
        "restricted": True,  # 小红书默认受限（反爬严格）
        "needs_paste": False,
        "url": url,
    }

    # ── 尝试提取结构化数据 ──
    state_data = extract_xiaohongshu_initial_state(html, url)
    title = ""
    author = ""
    desc = ""

    if state_data:
        title = state_data.get("title", "")
        author = state_data.get("nickname", "")
        desc = state_data.get("desc", "")

    # ── og:meta 标签回退 ──
    if not title:
        og_title = re.search(
            r'<meta[^>]*property="og:title"[^>]*content="([^"]*)"', html
        )
        if og_title:
            title = og_title.group(1).strip()

    if not desc:
        og_desc = re.search(
            r'<meta[^>]*property="og:description"[^>]*content="([^"]*)"', html
        )
        if og_desc:
            desc = og_desc.group(1).strip()

    # ── 提取页面文本内容（注意小红书正文可能在 <div id="detail-desc"> 中）──
    if not desc or len(desc) < 80:
        for pattern in [
            r'<div[^>]*id="detail-desc"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*note-text[^"]*"[^>]*>(.*?)</div>',
            r'<div[^>]*class="[^"]*content[^"]*"[^>]*>(.*?)</div>',
        ]:
            match = re.search(pattern, html, re.DOTALL)
            if match:
                text = re.sub(r'<[^>]+>', '', match.group(1))
                text = re.sub(r'\s+', ' ', text).strip()
                if len(text) > 40:  # 有意义的文本长度
                    desc = text
                    break

    # ── 构建摘要 ──
    parts = []
    if title:
        parts.append(f"标题：{title}")
    if author:
        parts.append(f"作者：{author}")
    if desc:
        parts.append(desc[:800])

    if not parts:
        return None

    summary = "\n".join(parts)

    return {
        **base_fields,
        "title": title or "小红书分享",
        "author": author or "小红书用户",
        "summary": summary[:1200],
        "platform": "xiaohongshu",
    }
