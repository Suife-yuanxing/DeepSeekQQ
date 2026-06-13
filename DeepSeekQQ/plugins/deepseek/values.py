"""价值体系加载和匹配 — bot的三观/独立意见系统。

从 values.json 加载bot在各种日常话题上的立场。
在用户消息中匹配话题关键词，检测价值冲突，生成意见提示注入prompt。

好感度分层表达策略：
- 陌生人(0-20): 10%概率轻描淡写
- 认识(20-50): 30%概率温和反驳
- 在意(50-200): 60%概率真实意见
- 重要(200-500): 80%概率直接观点
- 专属(500+): 90%概率完全真实
"""
import json
import os
import random
import re
import time
from typing import Dict
from typing import List
from typing import Optional


# ============================================================
# 价值体系加载
# ============================================================

_values_cache: Optional[dict] = None
_values_mtime: float = 0
_VALUES_CACHE_TTL: float = 60  # 1分钟TTL，支持热更新


def _load_values() -> dict:
    """加载 values.json，带缓存和热重载。"""
    global _values_cache, _values_mtime

    values_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "data", "persona", "values.json"
    )

    now = time.time()
    try:
        if os.path.exists(values_path):
            file_mtime = os.path.getmtime(values_path)
            if _values_cache is not None and file_mtime == _values_mtime and now - _values_mtime < _VALUES_CACHE_TTL:
                return _values_cache

            with open(values_path, "r", encoding="utf-8") as f:
                _values_cache = json.load(f)
            _values_mtime = file_mtime
            return _values_cache
    except Exception:
        pass

    # 文件不存在或加载失败：返回内置最小集合
    if _values_cache is None:
        _values_cache = _get_fallback_values()
    return _values_cache


def _get_fallback_values() -> dict:
    """内置最小价值体系（当 values.json 不可用时）。"""
    return {
        "categories": {
            "生活态度": {
                "奶茶": {"opinion": "奶茶是生命之源", "intensity": 4, "style": "热情", "keywords": ["奶茶"]},
                "早起": {"opinion": "早起毁一天", "intensity": 4, "style": "坚决", "keywords": ["早起"]},
            }
        },
        "conflict_mappings": {},
    }


# ============================================================
# 关键词匹配
# ============================================================

def _normalize(text: str) -> str:
    """标准化文本，去除表情符号和多余空格。"""
    return text.strip().lower()


def find_relevant_values(user_message: str) -> List[dict]:
    """在用户消息中匹配关键词，返回相关的价值条目列表。

    Returns:
        [{topic, opinion, intensity, style, keywords_matched, category}, ...]
        按 intensity 降序排列
    """
    values = _load_values()
    msg = _normalize(user_message)
    results = []

    for category_name, topics in values.get("categories", {}).items():
        for topic_name, topic_data in topics.items():
            keywords = topic_data.get("keywords", [])
            matched = [kw for kw in keywords if kw in msg]
            if matched:
                results.append({
                    "topic": topic_name,
                    "opinion": topic_data["opinion"],
                    "intensity": topic_data.get("intensity", 3),
                    "style": topic_data.get("style", "坦诚"),
                    "keywords_matched": matched,
                    "category": category_name,
                })

    # 按 intensity 降序
    results.sort(key=lambda x: x["intensity"], reverse=True)
    return results


def detect_value_conflicts(user_message: str, relevant_values: List[dict]) -> List[dict]:
    """检测用户观点是否与bot价值冲突。

    对每个匹配到的价值条目，检查用户消息中是否包含冲突关键词。

    Returns:
        [{topic, bot_opinion, intensity, style, opposing_matched, response_type}, ...]
    """
    values = _load_values()
    conflicts = values.get("conflict_mappings", {})
    msg = _normalize(user_message)

    results = []
    for rv in relevant_values:
        topic = rv["topic"]
        if topic in conflicts:
            conflict = conflicts[topic]
            opposing_kws = conflict.get("opposing_keywords", [])
            matched = [kw for kw in opposing_kws if kw in msg]
            if matched:
                results.append({
                    "topic": topic,
                    "bot_opinion": rv["opinion"],
                    "intensity": rv["intensity"],
                    "style": rv["style"],
                    "opposing_matched": matched,
                    "response_type": conflict.get("response_type", "温和反驳"),
                })

    return results


# ============================================================
# 好感度分层表达
# ============================================================

_AFFECTION_THRESHOLDS = [
    (500, 0.90, "完全真实"),      # 专属主人
    (200, 0.80, "直接观点"),      # 重要的人
    (50,  0.60, "真实意见"),      # 在意
    (20,  0.30, "温和反驳"),      # 认识
    (0,   0.10, "轻描淡写"),      # 陌生人
]


def _get_expression_chance(affection_score: float) -> tuple:
    """根据好感度返回表达概率和描述。"""
    for threshold, chance, desc in _AFFECTION_THRESHOLDS:
        if affection_score >= threshold:
            return chance, desc
    return 0.10, "轻描淡写"


# ============================================================
# 提示词生成（主入口）
# ============================================================

def get_value_hints(user_message: str, affection_score: float = 0) -> List[str]:
    """主入口：根据用户消息和好感度，生成注入prompt的价值提示词。

    Args:
        user_message: 用户消息原文
        affection_score: 好感度分数

    Returns:
        要注入系统提示词的文本列表，可能是空列表
    """
    if not user_message or len(user_message.strip()) < 3:
        return []

    try:
        relevant = find_relevant_values(user_message)
        if not relevant:
            return []

        conflicts = detect_value_conflicts(user_message, relevant)
        chance, _ = _get_expression_chance(affection_score)

        hints = []

        # 有冲突时：根据好感度决定是否表达不同意见
        if conflicts:
            for c in conflicts:
                if random.random() < chance:
                    style = c["style"]
                    hint = _build_conflict_hint(c, affection_score)
                    if hint:
                        hints.append(hint)
        else:
            # 无冲突但话题相关：bot可以自然地表达自己的看法（高好感度更主动）
            natural_chance = chance * 0.7  # 无冲突时更低调
            for rv in relevant[:2]:  # 最多2条
                if rv["intensity"] >= 3 and random.random() < natural_chance:
                    hints.append(
                        f"聊到了{rv['topic']}，你对此有自己的看法：{rv['opinion']}。"
                        f"自然地表达，不要强行说教。"
                    )

        return hints
    except Exception:
        return []


def _build_conflict_hint(conflict: dict, affection_score: float) -> Optional[str]:
    """为检测到的冲突生成合适的提示词。"""
    topic = conflict["topic"]
    bot_opinion = conflict["bot_opinion"]
    response_type = conflict.get("response_type", "温和反驳")
    style = conflict["style"]

    # 根据好感度选择表达风格
    if affection_score >= 500:
        # 专属：完全真实，可以撒娇式反驳
        return (
            f"用户对「{topic}」的看法和你不一致。"
            f"你完全不同意他的观点。你的立场是：{bot_opinion}。"
            f"用{style}的方式表达，可以带点撒娇或傲娇的语气，但立场要坚定。"
            f"像跟最亲近的人说话一样真实。"
        )
    elif affection_score >= 200:
        # 重要：直接表达
        return (
            f"用户对「{topic}」的看法和你不完全一致。"
            f"你的真实想法是：{bot_opinion}。"
            f"用{style}的方式自然地表达不同意见。不要太客气，像跟好朋友说话一样直接。"
        )
    elif affection_score >= 50:
        # 在意：表达真实想法但不要太冲
        return (
            f"用户说的关于「{topic}」的话，你不太同意。"
            f"你的想法是：{bot_opinion}。"
            f"可以温和地表达一下自己的观点，但不用争。"
        )
    else:
        # 陌生人/认识：轻描淡写
        return (
            f"用户对「{topic}」的看法和你的立场（{bot_opinion}）有点不同。"
            f"不用直接反驳，可以含糊地带过或者换个话题。保持礼貌距离。"
        )


def get_opinion_injection(user_message: str, affection_score: float = 0) -> str:
    """生成注入prompt的独立意见提示（给build_system_prompt用）。

    与 get_value_hints 的区别：这个返回完整的文本段落，可以直接拼接到prompt中。
    """
    hints = get_value_hints(user_message, affection_score)
    if not hints:
        return ""

    parts = ["【你的立场】"]
    for hint in hints:
        parts.append(f"- {hint}")
    return "\n".join(parts)
