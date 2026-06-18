"""群聊成员画像和社交记忆。"""
import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from .db_core import get_db

# ============================================================
# 动态群聊梗冷却 — 根据梗的热度调整冷却时间
# ============================================================

def get_dynamic_cooldown(meme: Dict[str, Any]) -> int:
    """获取动态冷却时间（秒）"""
    base_cooldown = 3600  # 1小时

    # 热度因子（使用次数越多，冷却越短）
    usage_count = meme.get('usage_count', 0)
    if usage_count > 10:
        heat_factor = 0.5  # 热门梗：30分钟
    elif usage_count > 5:
        heat_factor = 0.7  # 中等：42分钟
    else:
        heat_factor = 1.0  # 冷门：60分钟

    # 反馈因子（正面反馈多，冷却更短）
    positive_ratio = meme.get('positive_feedback_ratio', 0.5)
    if positive_ratio > 0.7:
        feedback_factor = 0.8
    elif positive_ratio < 0.3:
        feedback_factor = 1.5  # 负面反馈多，延长冷却
    else:
        feedback_factor = 1.0

    # 时间因子（最近用过，冷却更长）
    last_used = meme.get('last_used', 0)
    hours_since_use = (time.time() - last_used) / 3600
    if hours_since_use < 1:
        time_factor = 1.5
    elif hours_since_use < 3:
        time_factor = 1.0
    else:
        time_factor = 0.8  # 很久没用，可以再用

    cooldown = base_cooldown * heat_factor * feedback_factor * time_factor
    return int(max(600, min(7200, cooldown)))  # 限制在10分钟到2小时


async def get_or_create_member(group_id: str, member_id: str, nickname: str = "") -> Dict[str, Any]:
    """获取或创建群成员记录。"""
    db = await get_db()
    now = time.time()

    # 尝试获取
    async with db.execute(
        """SELECT nickname, last_active, relationship, personality_tags, talk_frequency
           FROM group_members WHERE group_id = ? AND member_id = ?""",
        (str(group_id), str(member_id))
    ) as cursor:
        row = await cursor.fetchone()

    if row:
        return {
            "nickname": row["nickname"],
            "last_active": row["last_active"],
            "relationship": row["relationship"],
            "personality_tags": json.loads(row["personality_tags"]) if row["personality_tags"] else [],
            "talk_frequency": row["talk_frequency"],
        }

    # 创建新记录
    try:
        await db.execute(
            """INSERT INTO group_members (group_id, member_id, nickname, last_active, relationship)
               VALUES (?, ?, ?, ?, 'stranger')
               ON CONFLICT(group_id, member_id) DO UPDATE SET last_active = ?""",
            (str(group_id), str(member_id), nickname, now, now)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise

    return {
        "nickname": nickname,
        "last_active": now,
        "relationship": "stranger",
        "personality_tags": [],
        "talk_frequency": 0,
    }


async def update_member_activity(group_id: str, member_id: str):
    """更新成员最后活跃时间。"""
    db = await get_db()
    now = time.time()
    try:
        await db.execute(
            """UPDATE group_members SET last_active = ? WHERE group_id = ? AND member_id = ?""",
            (now, str(group_id), str(member_id))
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def update_member_nickname(group_id: str, member_id: str, nickname: str):
    """更新成员昵称。"""
    db = await get_db()
    try:
        await db.execute(
            """UPDATE group_members SET nickname = ? WHERE group_id = ? AND member_id = ?""",
            (nickname, str(group_id), str(member_id))
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def add_personality_tag(group_id: str, member_id: str, tag: str):
    """为成员添加性格标签。"""
    db = await get_db()
    async with db.execute(
        """SELECT personality_tags FROM group_members WHERE group_id = ? AND member_id = ?""",
        (str(group_id), str(member_id))
    ) as cursor:
        row = await cursor.fetchone()

    if not row:
        return

    tags = json.loads(row["personality_tags"]) if row["personality_tags"] else []
    if tag not in tags:
        tags.append(tag)
        try:
            await db.execute(
                """UPDATE group_members SET personality_tags = ? WHERE group_id = ? AND member_id = ?""",
                (json.dumps(tags), str(group_id), str(member_id))
            )
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def get_active_members(group_id: str, hours: float = 24) -> List[Dict[str, Any]]:
    """获取最近活跃的群成员。"""
    db = await get_db()
    cutoff = time.time() - hours * 3600
    members = []
    async with db.execute(
        """SELECT member_id, nickname, relationship, personality_tags, talk_frequency
           FROM group_members WHERE group_id = ? AND last_active > ?
           ORDER BY last_active DESC""",
        (str(group_id), cutoff)
    ) as cursor:
        async for row in cursor:
            members.append({
                "member_id": row["member_id"],
                "nickname": row["nickname"],
                "relationship": row["relationship"],
                "personality_tags": json.loads(row["personality_tags"]) if row["personality_tags"] else [],
                "talk_frequency": row["talk_frequency"],
            })
    return members


async def get_recent_group_messages(group_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    """获取最近的群聊消息（从 memories 表）。

    B13 fix: memories 表不存储群成员的真实 user_id，role 列仅区分 user/assistant。
    对于群聊消息，user_id 设为空字符串，调用方应通过 group_members 表获取成员信息。
    """
    db = await get_db()
    messages = []
    async with db.execute(
        """SELECT role, content, timestamp FROM memories
           WHERE session_id = ? AND archived = 0 ORDER BY timestamp DESC LIMIT ?""",
        (f"group_{group_id}", limit)
    ) as cursor:
        async for row in cursor:
            messages.append({
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                # B13: role 列不存储真实 QQ 号，对非 bot 消息无法确定具体成员
                "user_id": "bot" if row["role"] == "assistant" else "",
            })
    messages.reverse()  # 按时间正序
    return messages
