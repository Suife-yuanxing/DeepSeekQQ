"""群聊气氛分析 — 判断当前群聊是否适合插话。

基于最近消息的密度、参与者数量、节奏间隔等判断。
"""
import time
from typing import List, Dict, Any


def should_join_conversation(recent_messages: List[Dict[str, Any]], bot_id: str) -> Dict[str, Any]:
    """分析最近群聊消息，决定是否参与。

    Args:
        recent_messages: 最近消息列表，每条需有 user_id, timestamp
        bot_id: bot 的 QQ 号

    Returns:
        {"should_reply": bool, "reason": str, "confidence": float}
    """
    if not recent_messages:
        return {"should_reply": False, "reason": "无消息", "confidence": 0}

    now = time.time()

    # 计算每条消息的年龄（秒）
    for msg in recent_messages:
        msg["age_seconds"] = now - msg.get("timestamp", now)

    # 最近 5 分钟的消息
    recent_5min = [m for m in recent_messages if m.get("age_seconds", 999) < 300]
    unique_users = set(m.get("user_id") for m in recent_5min)

    # 规则 1: 多人激烈讨论（>=3人，>=6条）→ 不抢话
    if len(unique_users) >= 3 and len(recent_5min) >= 6:
        return {"should_reply": False, "reason": "多人讨论中", "confidence": 0.8}

    # 规则 2: 最近 1 分钟只有一人说话 → 可以接话
    recent_1min = [m for m in recent_messages if m.get("age_seconds", 999) < 60]
    if len(recent_1min) == 1:
        return {"should_reply": True, "reason": "冷场中", "confidence": 0.6}

    # 规则 3: 最后一条消息超过 30 秒 → 节奏空隙
    last_msg_age = recent_messages[-1].get("age_seconds", 0) if recent_messages else 999
    if last_msg_age > 30:
        return {"should_reply": True, "reason": "节奏空隙", "confidence": 0.5}

    # 规则 4: 有人连续发了 3 条以上（刷屏）→ 不插话
    user_msg_count = {}
    for msg in recent_5min:
        uid = msg.get("user_id", "")
        user_msg_count[uid] = user_msg_count.get(uid, 0) + 1
    if any(count >= 3 for count in user_msg_count.values()):
        return {"should_reply": False, "reason": "有人刷屏", "confidence": 0.7}

    # 默认不插话
    return {"should_reply": False, "reason": "默认不插话", "confidence": 0.3}
