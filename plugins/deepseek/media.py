"""媒体分享模块 - 让回复更像真实人类。

功能：
- 检测回复中的 URL，提取并作为富文本发送
- 从搜索结果中提取图片/链接分享
- 生成带链接的自然回复
"""
import re
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass

from nonebot.adapters.onebot.v11 import MessageSegment, Message
from nonebot import logger


# ============================================================
# URL 检测
# ============================================================

_URL_PATTERN = re.compile(
    r'https?://[^\s<>"\')\]]+',
    re.IGNORECASE
)


def extract_urls(text: str) -> List[str]:
    """从文本中提取所有 URL。"""
    return _URL_PATTERN.findall(text)


def remove_urls(text: str) -> str:
    """从文本中移除 URL。"""
    return _URL_PATTERN.sub('', text).strip()


# ============================================================
# 搜索结果转分享内容
# ============================================================

@dataclass
class ShareableItem:
    """可分享的内容项"""
    title: str
    url: str
    snippet: str = ""
    image_url: str = ""  # 搜索结果中的图片


def extract_shareable_from_search(search_result) -> List[ShareableItem]:
    """从搜索结果中提取可分享的内容。"""
    if not search_result or not search_result.results:
        return []

    items = []
    for r in search_result.results[:3]:
        item = ShareableItem(
            title=r.get("title", ""),
            url=r.get("url", ""),
            snippet=r.get("snippet", "")[:150],
        )
        if item.url:
            items.append(item)
    return items


# ============================================================
# 回复后处理：分离文字和链接
# ============================================================

def split_reply_and_links(reply_text: str) -> Tuple[str, List[str]]:
    """将回复拆分为纯文字和链接列表。

    Returns:
        (clean_text, urls)
    """
    urls = extract_urls(reply_text)
    clean_text = remove_urls(reply_text)

    # 清理多余空行
    clean_text = re.sub(r'\n{3,}', '\n\n', clean_text).strip()

    return clean_text, urls


# ============================================================
# 构建富文本消息
# ============================================================

def build_rich_message(clean_text: str, urls: List[str], search_items: List[ShareableItem] = None) -> Message:
    """构建包含文字+链接的富文本消息。

    不使用 CQ 码转发链接（会被拦截），而是把链接自然融入文字。
    """
    msg = Message()

    # 文字部分
    if clean_text:
        msg += MessageSegment.text(clean_text)

    # 如果有搜索结果链接，附加到文字后面
    if search_items:
        link_lines = []
        for item in search_items[:2]:  # 最多2个链接
            if item.url:
                link_lines.append(f"📎 {item.title}\n{item.url}")
        if link_lines:
            msg += MessageSegment.text("\n\n" + "\n\n".join(link_lines))

    # 如果回复中自带 URL 且没有搜索结果链接，也加上
    elif urls:
        for url in urls[:2]:
            msg += MessageSegment.text(f"\n{url}")

    return msg


# ============================================================
# Prompt 辅助：告诉 AI 如何自然地分享内容
# ============================================================

MEDIA_PROMPT_HINT = """当你想分享一个有趣的内容时：
- 可以直接把链接放在回复里，像发QQ消息一样自然
- 不要说"以下是链接"，直接发就行
- 如果有图片，描述一下就好，不需要特别说明
- 分享时用你自己的语气吐槽或评论，不要像转发新闻"""


def get_media_prompt_hint() -> str:
    """返回给 prompt 的媒体分享提示。"""
    return MEDIA_PROMPT_HINT
