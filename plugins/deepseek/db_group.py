"""群聊成员画像和社交记忆。"""
import time
import json
from typing import Dict, Any, Optional, List

from .db_core import get_db


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
    await db.execute(
        """INSERT INTO group_members (group_id, member_id, nickname, last_active, relationship)
           VALUES (?, ?, ?, ?, 'stranger')
           ON CONFLICT(group_id, member_id) DO UPDATE SET last_active = ?""",
        (str(group_id), str(member_id), nickname, now, now)
    )
    await db.commit()

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
    await db.execute(
        """UPDATE group_members SET last_active = ? WHERE group_id = ? AND member_id = ?""",
        (now, str(group_id), str(member_id))
    )
    await db.commit()


async def update_member_nickname(group_id: str, member_id: str, nickname: str):
    """更新成员昵称。"""
    db = await get_db()
    await db.execute(
        """UPDATE group_members SET nickname = ? WHERE group_id = ? AND member_id = ?""",
        (nickname, str(group_id), str(member_id))
    )
    await db.commit()


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
        await db.execute(
            """UPDATE group_members SET personality_tags = ? WHERE group_id = ? AND member_id = ?""",
            (json.dumps(tags), str(group_id), str(member_id))
        )
        await db.commit()


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
    """获取最近的群聊消息（从 memories 表）。"""
    db = await get_db()
    messages = []
    async with db.execute(
        """SELECT role, content, timestamp FROM memories
           WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?""",
        (f"group_{group_id}", limit)
    ) as cursor:
        async for row in cursor:
            messages.append({
                "role": row["role"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "user_id": row["role"] if row["role"] != "assistant" else "bot",
            })
    messages.reverse()  # 按时间正序
    return messages
