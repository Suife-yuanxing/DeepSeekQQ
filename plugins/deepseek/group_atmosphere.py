"""群聊气氛分析 — 判断当前群聊是否适合插话、群聊角色定位。

基于最近消息的密度、参与者数量、节奏间隔等判断。
"""
import time
import random
from typing import List, Dict, Any, Optional


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


# ============================================================
# 群聊角色定位
# ============================================================

async def get_group_role_hint(group_id: str) -> str:
    """根据群聊情况决定 bot 的角色定位。

    - 人少（<5）：活跃分子，可以多插话
    - 人中等（5-10）：适度参与
    - 人多（>10）：安静观察者，少说话
    - 有熟人：更活跃
    - 全是生人：更谨慎
    """
    try:
        from .db_group import get_active_members
        from .db_social import get_relationships

        active = await get_active_members(group_id, hours=72)
        total_active = len(active)

        # 统计有关系的成员数
        friend_count = 0
        for member in active[:10]:  # 只检查前10个活跃成员
            rels = await get_relationships(group_id, member["member_id"])
            for rel in rels:
                if rel["rel_type"] in ("friend", "close", "teammate") and rel["strength"] >= 0.2:
                    friend_count += 1
                    break

        if total_active <= 3:
            return "群里人很少，你可以活跃一点，多参与聊天，像群里的活跃分子。"
        elif total_active <= 8:
            if friend_count >= 2:
                return "群里有几个你认识的人，可以自然地参与聊天，偶尔调侃一下。"
            return "群里人不多，可以适当参与，但不要每条都回。"
        elif total_active <= 15:
            if friend_count >= 3:
                return "群里人不少但有熟人，可以在感兴趣的话题时插话。"
            return "群里人比较多，安静观察为主，被@或有明确话题时再说话。"
        else:
            return "群里很热闹，你主要当观众，只有被@或特别有趣的话题才参与。"
    except Exception:
        return ""


async def get_group_social_context(group_id: str, current_msg: str) -> Dict[str, str]:
    """收集群聊社交上下文（关系、梗、角色），供 prompt 注入。

    Returns:
        {"social_hint", "meme_hint", "role_hint"}
    """
    result = {"social_hint": "", "meme_hint": "", "role_hint": ""}

    try:
        # 社交关系摘要
        from .db_social import get_group_relationships_summary, get_group_meme_hint
        result["social_hint"] = await get_group_relationships_summary(group_id)

        # 群聊梗匹配
        meme = await get_group_meme_hint(group_id, current_msg)
        if meme:
            result["meme_hint"] = meme

        # 角色定位
        result["role_hint"] = await get_group_role_hint(group_id)
    except Exception:
        pass

    return result
