"""智能上下文管理 — 替代简单的"保留最近N条"策略。

选择最相关、最重要的消息作为上下文，而非仅按时间顺序。
同时提供 token 预算管理和摘要缓存。
"""
import re
import time
from collections import OrderedDict
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger

# ============================================================
# 智能消息选择
# ============================================================

def select_context_messages(
    messages: List[Dict[str, Any]],
    current_msg: str,
    max_count: int = 10,
) -> List[Dict[str, Any]]:
    """智能选择上下文消息，替代简单的"保留最近N条"。

    选择策略（按优先级）：
    1. 最近 5 条（保证即时上下文连贯）
    2. 与当前消息语义相关的旧消息（关键词重叠）
    3. 情绪明显的消息（包含情绪关键词）
    4. 包含关键信息的消息（数字/日期/人名）

    Args:
        messages: 按时间正序排列的消息列表
        current_msg: 当前用户消息
        max_count: 最大选择数量

    Returns:
        选择后的消息列表（按时间正序）
    """
    if len(messages) <= max_count:
        return messages

    # 提取当前消息关键词
    current_keywords = set(re.findall(r'[一-龥]{2,6}', current_msg))
    current_keywords.update(re.findall(r'[a-zA-Z]{3,}', current_msg.lower()))

    # 分数计算
    scored = []
    total = len(messages)

    for i, msg in enumerate(messages):
        score = 0.0
        content = msg.get("content", "")

        # 1. 时间衰减：越新越高分（最近5条保底）
        recency_score = i / total  # 0~1，越大越新
        if i >= total - 5:
            score += 10.0  # 最近5条保底
        else:
            score += recency_score * 5.0

        # 2. 语义相关性：关键词重叠
        msg_keywords = set(re.findall(r'[一-龥]{2,6}', content))
        overlap = len(current_keywords & msg_keywords)
        score += overlap * 2.0

        # 3. 情绪明显的消息更有价值
        emotion_kw = ["开心", "难过", "生气", "喜欢", "讨厌", "爱", "恨",
                       "累", "烦", "怕", "哭", "笑", "感动", "担心"]
        if any(kw in content for kw in emotion_kw):
            score += 1.5

        # 4. 包含关键信息（数字/日期/人名）
        if re.search(r'\d{4}年|\d{1,2}月|\d{1,2}日|\d{1,2}:\d{2}', content):
            score += 1.0  # 日期
        if re.search(r'\d+', content):
            score += 0.5  # 数字

        # 5. 用户消息比 bot 回复更重要（信息量更大）
        if msg.get("role") == "user":
            score += 0.5

        # 6. 较长的消息通常信息量更大
        if len(content) > 50:
            score += 0.5

        scored.append((i, score, msg))

    # B20: 硬保底 — 最近 5 条始终保留，不受分数影响
    guaranteed = set(range(max(0, total - 5), total))
    # 按分数排序，取 top N
    scored.sort(key=lambda x: x[1], reverse=True)
    selected = []
    selected_indices = set()
    # 先选高分的（跳过已在保底中的，后面统一添加）
    for idx, score, msg in scored:
        if len(selected) >= max_count:
            break
        selected.append((idx, score, msg))
        selected_indices.add(idx)
    # 确保最近 5 条全部在内
    for idx in guaranteed:
        if idx not in selected_indices:
            # 找到对应条目并加入
            for s in scored:
                if s[0] == idx:
                    selected.append(s)
                    break
    # 如果超出 max_count，裁剪低分项（保留保底项）
    if len(selected) > max_count:
        # 保底项强制保留
        must_keep = [s for s in selected if s[0] in guaranteed]
        can_drop = [s for s in selected if s[0] not in guaranteed]
        # 按分数升序排列可丢弃的（低分在前）
        can_drop.sort(key=lambda x: x[1])
        # 保留足够的可丢弃项以填满 max_count
        keep_drop = can_drop[-(max_count - len(must_keep)):] if max_count > len(must_keep) else []
        selected = must_keep + keep_drop

    # 按原始顺序排列（保证对话连贯）
    selected.sort(key=lambda x: x[0])

    return [item[2] for item in selected]


# ============================================================
# Token 预算管理
# ============================================================

from .token_utils import estimate_tokens  # noqa: F811 — 共享实现，保留此导入以明确依赖


def estimate_message_tokens(messages: List[Dict[str, str]]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(content) + 4  # role + 格式开销
    return total


def fit_messages_to_budget(
    messages: List[Dict[str, str]],
    system_prompt: str,
    max_input_tokens: int = None,
    reserve_output: int = None,
) -> List[Dict[str, str]]:
    """将消息列表裁剪到 token 预算内。

    Args:
        messages: 消息列表（包含 system + history + user）
        system_prompt: system prompt 文本
        max_input_tokens: 最大输入 token 数（默认从 config 读取，28K）
        reserve_output: 预留给输出的 token 数（默认从 config 读取，2K）

    Returns:
        裁剪后的消息列表
    """
    from .config import MAX_INPUT_TOKENS, RESERVE_OUTPUT_TOKENS
    if max_input_tokens is None:
        max_input_tokens = MAX_INPUT_TOKENS
    if reserve_output is None:
        reserve_output = RESERVE_OUTPUT_TOKENS
    system_tokens = estimate_tokens(system_prompt) + 4
    available = max_input_tokens - system_tokens - reserve_output

    if available <= 0:
        # system prompt 太长，只保留最后 1 条用户消息
        user_msgs = [m for m in messages if m.get("role") == "user"]
        if user_msgs:
            return [messages[0], user_msgs[-1]]  # system + last user
        return messages[:2]

    # 从后往前保留消息，直到预算用完
    total = 0
    selected = []
    for msg in reversed(messages):
        msg_tokens = estimate_tokens(msg.get("content", "")) + 4
        if total + msg_tokens > available:
            break
        selected.append(msg)
        total += msg_tokens

    selected.reverse()

    # 确保至少有 system + 最后一条 user
    if len(selected) < 2 and len(messages) >= 2:
        selected = [messages[0], messages[-1]]

    return selected


# ============================================================
# 上下文摘要缓存
# ============================================================

# 摘要缓存：session_id -> (summary, message_count, timestamp)
_summary_cache: OrderedDict = OrderedDict()
_CACHE_MAX_SIZE = 100
_CACHE_EXPIRE_MESSAGES = 10  # 新消息超过 10 条时刷新


def get_cached_summary(session_id: str, current_msg_count: int) -> Optional[str]:
    """获取缓存的对话摘要（如果仍然有效）。"""
    if session_id not in _summary_cache:
        return None

    summary, cached_count, cached_time = _summary_cache[session_id]

    # 新消息超过阈值，缓存失效
    if current_msg_count - cached_count > _CACHE_EXPIRE_MESSAGES:
        del _summary_cache[session_id]
        return None

    # 超过 1 小时，缓存失效
    if time.time() - cached_time > 3600:
        del _summary_cache[session_id]
        return None

    return summary


def set_cached_summary(session_id: str, summary: str, msg_count: int):
    """设置摘要缓存。"""
    global _summary_cache
    _summary_cache[session_id] = (summary, msg_count, time.time())

    # LRU 淘汰
    while len(_summary_cache) > _CACHE_MAX_SIZE:
        _summary_cache.popitem(last=False)

    logger.debug(f"[上下文] 摘要缓存更新: {session_id[:20]}...")


# ============================================================
# 上下文优化报告
# ============================================================

def get_context_stats(
    messages: List[Dict[str, Any]],
    selected: List[Dict[str, Any]],
    system_prompt: str,
) -> Dict[str, Any]:
    """生成上下文优化统计报告。"""
    total_tokens = estimate_message_tokens(messages)
    selected_tokens = estimate_message_tokens(selected)
    system_tokens = estimate_tokens(system_prompt)

    return {
        "original_count": len(messages),
        "selected_count": len(selected),
        "compression_ratio": len(selected) / max(1, len(messages)),
        "original_tokens": total_tokens,
        "selected_tokens": selected_tokens,
        "system_tokens": system_tokens,
        "total_input_tokens": system_tokens + selected_tokens,
        "token_saved": total_tokens - selected_tokens,
    }


