"""表情包管理模块（Phase 5）。

功能：
- 加载表情包标签库
- 解析回复中的表情包标签 [sticker:emotion]
- 根据情绪从对应池中随机选图
- 发送表情包
"""
import os
import re
import json
import random
from pathlib import Path
from typing import List, Tuple, Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment, Message

from .config import STICKER_DIR, STICKER_ENABLED

# ============================================================
# 标签库
# ============================================================

_tags: dict = {}  # filename -> emotion_tag
_loaded: bool = False

# 情绪标签 → 可接受的备选标签（主标签没图时 fallback）
_EMOTION_FALLBACK = {
    "happy": ["happy", "cute", "excited", "default"],
    "angry": ["angry", "tsundere", "default"],
    "shy": ["shy", "cute", "love", "default"],
    "sad": ["sad", "shy", "default"],
    "tsundere": ["tsundere", "angry", "default"],
    "cute": ["cute", "happy", "shy", "default"],
    "funny": ["funny", "happy", "excited", "default"],
    "love": ["love", "shy", "cute", "default"],
    "speechless": ["speechless", "tsundere", "funny", "default"],
    "excited": ["excited", "happy", "funny", "default"],
}


def _load_tags():
    """加载标签库。"""
    global _tags, _loaded
    if _loaded:
        return

    tag_file = os.path.join(STICKER_DIR, "sticker_tags.json")
    if os.path.exists(tag_file):
        try:
            with open(tag_file, 'r', encoding='utf-8') as f:
                _tags = json.load(f)
            logger.info(f"[表情包] 加载了 {len(_tags)} 个标签")
        except Exception as e:
            logger.error(f"[表情包] 标签加载失败: {e}")

    _loaded = True


def select_sticker(emotion: str) -> Optional[str]:
    """根据情绪从对应池中随机选一张表情包路径。"""
    _load_tags()
    if not _tags:
        return None

    # 尝试主标签 → fallback 标签
    fallbacks = _EMOTION_FALLBACK.get(emotion, [emotion, "default"])

    for tag in fallbacks:
        candidates = [fn for fn, t in _tags.items() if t == tag]
        if candidates:
            chosen = random.choice(candidates)
            path = os.path.join(STICKER_DIR, chosen)
            if os.path.exists(path):
                return path

    # 最终 fallback：从所有图中随机选
    all_files = list(_tags.keys())
    if all_files:
        chosen = random.choice(all_files)
        path = os.path.join(STICKER_DIR, chosen)
        if os.path.exists(path):
            return path

    return None


def parse_sticker_tag(text: str) -> Tuple[str, Optional[str]]:
    """从回复中解析表情包标签。

    Returns:
        (clean_text, emotion_or_None)
    """
    # 匹配 [sticker:emotion] 或 [sticker]
    match = re.search(r'\[sticker:(\w+)\]', text)
    if match:
        clean = re.sub(r'\[sticker:\w+\]', '', text).strip()
        return clean, match.group(1)

    match = re.search(r'\[sticker\]', text)
    if match:
        clean = re.sub(r'\[sticker\]', '', text).strip()
        return clean, "default"

    return text, None


def should_send_sticker_fallback(reply_text: str, emotion_hint: str = None) -> Optional[str]:
    """如果 LLM 没有嵌入标签，根据概率和情绪决定是否发送。

    Returns:
        emotion tag 或 None
    """
    if not STICKER_ENABLED:
        return None

    # 30% 概率 fallback
    if random.random() > 0.30:
        return None

    # 根据回复内容推断情绪
    if emotion_hint:
        return emotion_hint

    # 简单关键词推断
    text = reply_text.lower()
    if any(w in text for w in ["哈哈", "笑", "lol", "😂", "搞笑"]):
        return "funny"
    if any(w in text for w in ["哼", "才不是", "笨蛋", "讨厌"]):
        return "tsundere"
    if any(w in text for w in ["喜欢", "爱你", "❤", "💕"]):
        return "love"
    if any(w in text for w in ["呜", "难过", "伤心", "哭"]):
        return "sad"
    if any(w in text for w in ["生气", "烦", "滚", "气死"]):
        return "angry"
    if any(w in text for w in ["害羞", "脸红", "哪有"]):
        return "shy"

    return "default"
