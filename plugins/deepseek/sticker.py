"""表情包管理模块（Phase 5）。

功能：
- 加载表情包标签库
- 解析回复中的表情包标签 [sticker:emotion]
- 根据情绪从对应池中随机选图
- 联网检索补充
- 发送表情包
"""
import os
import re
import json
import random
from pathlib import Path
from typing import List, Tuple, Optional, Dict

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


async def select_sticker_with_search(emotion: str) -> Optional[str]:
    """先尝试本地选图，找不到合适的则联网搜索。"""
    # 先尝试本地
    local_path = select_sticker(emotion)
    if local_path:
        # 检查是否只是 fallback 到了 default（说明没有精确匹配）
        fallbacks = _EMOTION_FALLBACK.get(emotion, [emotion, "default"])
        _load_tags()
        # 如果主标签有图，直接用本地的
        primary_candidates = [fn for fn, t in _tags.items() if t == emotion]
        if primary_candidates:
            return local_path

    # 本地没有精确匹配，尝试联网搜索
    try:
        from .sticker_search import search_sticker_online
        web_path = await search_sticker_online(emotion)
        if web_path:
            logger.info(f"[表情包] 联网检索成功: {emotion} -> {os.path.basename(web_path)}")
            return web_path
    except Exception as e:
        logger.warning(f"[表情包] 联网检索失败: {e}")

    # 联网也失败，用本地的 fallback
    return local_path


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


# ---------- 连续表情包追踪 ----------
_last_sticker_session: Dict[str, int] = {}  # session_id -> 连续发送次数
MAX_CONSECUTIVE_STICKERS = 1  # 最多连续发1张，第2张强制不发

STICKER_KEEP_PROBABILITY = 0.25  # LLM 加了标签时，保留概率 25%


def filter_sticker_tag(reply_text: str, session_id: str = "") -> Tuple[str, bool]:
    """后置过滤：LLM 返回带 [sticker:xxx] 的回复后，概率性剥掉标签。

    Returns:
        (text, should_send_sticker)
    """
    clean_text, emotion = parse_sticker_tag(reply_text)
    if not emotion:
        # LLM 没加标签，不需要过滤
        return reply_text, False

    # 连续发送检查
    if session_id:
        consecutive = _last_sticker_session.get(session_id, 0)
        if consecutive >= MAX_CONSECUTIVE_STICKERS:
            _last_sticker_session[session_id] = 0
            logger.info(f"[表情包] 连续{consecutive}张已达上限，本次跳过")
            return clean_text, False

    # 概率过滤：35% 保留，65% 剥掉
    import random
    if random.random() < STICKER_KEEP_PROBABILITY:
        # 保留，更新连续计数
        if session_id:
            _last_sticker_session[session_id] = _last_sticker_session.get(session_id, 0) + 1
        return reply_text, True
    else:
        # 剥掉标签，重置连续计数
        if session_id:
            _last_sticker_session[session_id] = 0
        logger.info(f"[表情包] 概率过滤：剥掉 {emotion} 标签")
        return clean_text, False


def should_send_sticker_fallback(reply_text: str, emotion_hint: str = None) -> Optional[str]:
    """如果 LLM 没有嵌入标签，根据概率和情绪决定是否发送。

    Returns:
        emotion tag 或 None
    """
    if not STICKER_ENABLED:
        return None

    # 15% 概率 fallback
    if random.random() > 0.15:
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
