"""表情包管理模块（Phase 5）。

功能：
- 加载表情包标签库
- 解析回复中的表情包标签 [sticker:emotion]
- 根据情绪从对应池中随机选图
- 联网检索补充
- 发送表情包
"""
import json
import os
import random
import re
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment

from .config import MAX_CONSECUTIVE_STICKERS
from .config import STICKER_DIR
from .config import STICKER_ENABLED
from .config import STICKER_KEEP_PROBABILITY

# ============================================================
# 标签库（支持 v1 单标签 + v2 多标签+场景）
# ============================================================

_tags: dict = {}  # filename -> {"tags": [...], "scenes": [...]} (v2)
_loaded: bool = False

# 中文情绪 → 英文情绪映射（兜底，防止 LLM 输出中文标签）
_CN_EMOTION_MAP = {
    "开心": "happy", "高兴": "happy", "快乐": "happy",
    "生气": "angry", "愤怒": "angry", "发火": "angry",
    "害羞": "shy", "不好意思": "shy", "脸红": "shy",
    "难过": "sad", "伤心": "sad", "哭": "sad", "委屈": "sad",
    "傲娇": "tsundere", "嘴硬": "tsundere",
    "可爱": "cute", "萌": "cute", "卖萌": "cute",
    "搞笑": "funny", "好笑": "funny", "吐槽": "funny",
    "喜欢": "love", "爱你": "love", "撒娇": "love", "心动": "love",
    "无语": "speechless", "震惊": "speechless", "懵": "speechless",
    "兴奋": "excited", "激动": "excited", "期待": "excited",
}


def _normalize_emotion(emotion: str) -> str:
    """将中文情绪标签转为英文，英文原样返回。"""
    if not emotion:
        return emotion
    return _CN_EMOTION_MAP.get(emotion, emotion)


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


def _normalize_tag_entry(entry) -> dict:
    """将 v1 单标签格式统一转换为 v2 格式。"""
    if isinstance(entry, dict):
        # v2 格式：{"tags": [...], "scenes": [...]}
        return {
            "tags": entry.get("tags", ["default"]),
            "scenes": entry.get("scenes", ["日常"]),
        }
    elif isinstance(entry, str):
        # v1 格式："cute" -> 自动转换
        return {
            "tags": [entry],
            "scenes": ["日常"],
        }
    return {"tags": ["default"], "scenes": ["日常"]}


def _load_tags():
    """加载标签库（兼容 v1/v2 格式）。"""
    global _tags, _loaded
    if _loaded:
        return

    tag_file = os.path.join(STICKER_DIR, "sticker_tags.json")
    if os.path.exists(tag_file):
        try:
            with open(tag_file, 'r', encoding='utf-8') as f:
                raw = json.load(f)
            # 统一转换为 v2 格式
            _tags = {fn: _normalize_tag_entry(entry) for fn, entry in raw.items()}
            logger.info(f"[表情包] 加载了 {len(_tags)} 个标签 (v2格式)")
        except Exception as e:
            logger.error(f"[表情包] 标签加载失败: {e}")

    _loaded = True


def select_sticker(emotion: str, scene: str = "") -> Optional[str]:
    """根据情绪和场景从对应池中随机选一张表情包路径。

    匹配优先级：
    1. scene 精确匹配（如果提供了 scene）
    2. emotion 标签匹配（走 fallback chain）
    3. 随机 fallback
    """
    _load_tags()
    if not _tags:
        return None

    # 优先级1：scene 精确匹配
    if scene:
        scene_candidates = [
            fn for fn, entry in _tags.items()
            if scene in entry.get("scenes", [])
        ]
        if scene_candidates:
            chosen = random.choice(scene_candidates)
            path = os.path.join(STICKER_DIR, chosen)
            if os.path.exists(path):
                return path

    # 优先级2：emotion 标签匹配（fallback chain）
    fallbacks = _EMOTION_FALLBACK.get(emotion, [emotion, "default"])
    for tag in fallbacks:
        candidates = [
            fn for fn, entry in _tags.items()
            if tag in entry.get("tags", [])
        ]
        if candidates:
            chosen = random.choice(candidates)
            path = os.path.join(STICKER_DIR, chosen)
            if os.path.exists(path):
                return path

    # 优先级3：随机 fallback
    all_files = list(_tags.keys())
    if all_files:
        chosen = random.choice(all_files)
        path = os.path.join(STICKER_DIR, chosen)
        if os.path.exists(path):
            return path

    return None


async def select_sticker_with_search(emotion: str, scene: str = "") -> Optional[str]:
    """先尝试本地选图（scene优先），找不到合适的则联网搜索。"""
    _load_tags()

    # 本地优先：按 scene 精确匹配
    if scene:
        local_path = select_sticker(emotion, scene)
        # 检查是否真的匹配到了 scene
        scene_candidates = [
            fn for fn, entry in _tags.items()
            if scene in entry.get("scenes", [])
        ]
        if scene_candidates:
            return local_path

    # 本地次优：按 emotion 匹配
    local_path = select_sticker(emotion)
    primary_candidates = [
        fn for fn, entry in _tags.items()
        if emotion in entry.get("tags", [])
    ]
    if primary_candidates:
        return local_path

    # 联网兜底：用 scene+emotion 搜索
    try:
        from .sticker_search import search_sticker_online
        search_keyword = f"{scene} {emotion}" if scene else emotion
        web_path = await search_sticker_online(search_keyword)
        if web_path:
            logger.info(f"[表情包] 联网检索成功: {search_keyword} -> {os.path.basename(web_path)}")
            return web_path
    except Exception as e:
        logger.warning(f"[表情包] 联网检索失败: {e}")

    # 最终 fallback
    return local_path


def parse_sticker_tag(text: str) -> Tuple[str, Optional[str], str]:
    """从回复中解析表情包标签。

    支持格式：
    - [sticker:emotion|scene]  — 情绪+场景
    - [sticker:emotion]        — 仅情绪
    - [sticker]                — 默认

    Returns:
        (clean_text, emotion_or_None, scene_or_empty)
    """
    # 匹配 [sticker:emotion|scene]
    match = re.search(r'\[sticker:(\w+)\|([^\]]+)\]', text)
    if match:
        clean = re.sub(r'\[sticker:\w+\|[^\]]+\]', '', text).strip()
        return clean, _normalize_emotion(match.group(1)), match.group(2).strip()

    # 匹配 [sticker:emotion]
    match = re.search(r'\[sticker:(\w+)\]', text)
    if match:
        clean = re.sub(r'\[sticker:\w+\]', '', text).strip()
        return clean, _normalize_emotion(match.group(1)), ""

    # 匹配 [sticker]
    match = re.search(r'\[sticker\]', text)
    if match:
        clean = re.sub(r'\[sticker\]', '', text).strip()
        return clean, "default", ""

    return text, None, ""


# ---------- 连续表情包追踪 ----------
_last_sticker_session: Dict[str, int] = {}  # session_id -> 连续发送次数


def filter_sticker_tag(reply_text: str, session_id: str = "",
                       keep_probability: float = None) -> Tuple[str, bool]:
    """后置过滤：LLM 返回带 [sticker:xxx] 的回复后，概率性剥掉标签。

    Args:
        keep_probability: 动态保留概率（功能⑤情绪驱动），None 时用配置默认值。

    Returns:
        (text, should_send_sticker)
    """
    # 定期清理缓存，防止内存泄漏
    if len(_last_sticker_session) > 500:
        keys = list(_last_sticker_session.keys())
        for k in keys[:len(keys) - 200]:
            del _last_sticker_session[k]

    clean_text, emotion, scene = parse_sticker_tag(reply_text)
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

    # 概率过滤（功能⑤：使用情绪驱动的动态概率）
    prob = keep_probability if keep_probability is not None else STICKER_KEEP_PROBABILITY
    if random.random() < prob:
        # 保留，更新连续计数
        if session_id:
            _last_sticker_session[session_id] = _last_sticker_session.get(session_id, 0) + 1
        return reply_text, True
    else:
        # 剥掉标签，重置连续计数
        if session_id:
            _last_sticker_session[session_id] = 0
        logger.info(f"[表情包] 概率过滤：剥掉 {emotion} 标签 (prob={prob:.2f})")
        return clean_text, False


def should_send_sticker_fallback(reply_text: str, emotion_hint: str = None,
                                  fallback_chance: float = None) -> Optional[str]:
    """如果 LLM 没有嵌入标签，根据概率和情绪决定是否发送。

    Args:
        fallback_chance: 动态 fallback 概率（功能⑤情绪驱动），None 时用默认 15%。

    Returns:
        emotion tag 或 None
    """
    if not STICKER_ENABLED:
        return None

    # 动态 fallback 概率（功能⑤：情绪越积极越容易发表情包）
    base_chance = fallback_chance if fallback_chance is not None else 0.25
    if random.random() > base_chance:
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
