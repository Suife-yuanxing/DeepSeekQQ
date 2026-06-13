"""社交关系数据库操作 — 社交关系图、群聊梗、社交记忆。

支撑群聊场景的社交能力：
- group_social_graph: 成员间的关系（朋友/对手/密友/陌生人）
- group_memes: 群聊专属梗和暗号
- social_references: 用户提到的社交圈人物
"""
import json
import random
import re
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .db_core import get_db

# ============================================================
# 社交关系图 (group_social_graph)
# ============================================================

# 关系类型和对应的行为提示
RELATIONSHIP_TYPES = {
    "friend": {"label": "朋友", "hint": "他们关系不错，互动时可以提到"},
    "close": {"label": "好友", "hint": "他们关系很好，经常一起出现"},
    "rival": {"label": "对手", "hint": "他们有点竞争关系，可能会互怼"},
    "couple": {"label": "情侣", "hint": "他们是一对，注意别当电灯泡"},
    "teammate": {"label": "队友", "hint": "他们经常一起玩游戏/做事"},
    "stranger": {"label": "陌生人", "hint": ""},
}


async def record_relationship(
    group_id: str,
    member_a: str,
    member_b: str,
    rel_type: str = "friend",
    evidence: str = "",
):
    """记录或更新两个成员之间的关系。

    关系强度随互动次数增强。
    """
    if member_a == member_b:
        return
    # 规范化：保证 (a, b) 顺序一致
    if member_a > member_b:
        member_a, member_b = member_b, member_a

    db = await get_db()
    now = time.time()

    async with db.execute(
        """SELECT id, strength, interaction_count, rel_type FROM group_social_graph
           WHERE group_id = ? AND member_a = ? AND member_b = ?""",
        (str(group_id), str(member_a), str(member_b))
    ) as cursor:
        existing = await cursor.fetchone()

    if existing:
        new_strength = min(1.0, existing["strength"] + 0.05)
        new_count = existing["interaction_count"] + 1
        # 关系升级：互动多了可能从 stranger → friend → close
        new_type = _maybe_upgrade_rel(existing["rel_type"], new_count)
        await db.execute(
            """UPDATE group_social_graph
               SET strength = ?, interaction_count = ?, rel_type = ?,
                   last_interaction = ?, evidence = ?
               WHERE id = ?""",
            (new_strength, new_count, new_type, now, evidence[:200] or existing["evidence"], existing["id"])
        )
    else:
        await db.execute(
            """INSERT INTO group_social_graph
               (group_id, member_a, member_b, rel_type, strength, evidence,
                interaction_count, created_at, last_interaction)
               VALUES (?, ?, ?, ?, 0.1, ?, 1, ?, ?)""",
            (str(group_id), str(member_a), str(member_b), rel_type,
             evidence[:200], now, now)
        )
    await db.commit()
    logger.debug(f"[社交] 记录关系: {member_a[:6]}-{member_b[:6]} = {rel_type}")


def _maybe_upgrade_rel(current_type: str, interaction_count: int) -> str:
    """根据互动次数判断是否升级关系。"""
    if current_type == "stranger" and interaction_count >= 5:
        return "friend"
    if current_type == "friend" and interaction_count >= 20:
        return "close"
    return current_type


async def get_relationships(group_id: str, member_id: str) -> List[Dict[str, Any]]:
    """获取某成员的所有社交关系。"""
    db = await get_db()
    async with db.execute(
        """SELECT member_a, member_b, rel_type, strength, interaction_count, evidence
           FROM group_social_graph
           WHERE group_id = ? AND (member_a = ? OR member_b = ?)
           ORDER BY strength DESC""",
        (str(group_id), str(member_id), str(member_id))
    ) as cursor:
        rows = await cursor.fetchall()

    results = []
    for row in rows:
        other = row["member_b"] if row["member_a"] == member_id else row["member_a"]
        results.append({
            "other_member": other,
            "rel_type": row["rel_type"],
            "strength": row["strength"],
            "interaction_count": row["interaction_count"],
            "evidence": row["evidence"],
        })
    return results


async def get_relationship(group_id: str, member_a: str, member_b: str) -> Optional[Dict[str, Any]]:
    """获取两个成员之间的关系。"""
    if member_a > member_b:
        member_a, member_b = member_b, member_a
    db = await get_db()
    async with db.execute(
        """SELECT rel_type, strength, interaction_count, evidence
           FROM group_social_graph
           WHERE group_id = ? AND member_a = ? AND member_b = ?""",
        (str(group_id), str(member_a), str(member_b))
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return None
    return {
        "rel_type": row["rel_type"],
        "strength": row["strength"],
        "interaction_count": row["interaction_count"],
        "evidence": row["evidence"],
    }


async def get_group_relationships_summary(group_id: str) -> str:
    """获取群内社交关系的简要摘要，用于注入 prompt。"""
    db = await get_db()
    async with db.execute(
        """SELECT member_a, member_b, rel_type, strength
           FROM group_social_graph
           WHERE group_id = ? AND rel_type != 'stranger' AND strength >= 0.2
           ORDER BY strength DESC LIMIT 10""",
        (str(group_id),)
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return ""

    rels = []
    for row in rows:
        info = RELATIONSHIP_TYPES.get(row["rel_type"], {})
        label = info.get("label", row["rel_type"])
        rels.append(f"{row['member_a'][:6]}和{row['member_b'][:6]}是{label}")

    return "群内关系：" + "；".join(rels)


async def decay_relationships(inactive_days: int = 30, decay_amount: float = 0.02):
    """对长期不活跃的关系做衰减。"""
    db = await get_db()
    cutoff = time.time() - inactive_days * 86400
    await db.execute(
        """UPDATE group_social_graph SET strength = MAX(0.05, strength - ?)
           WHERE last_interaction < ? AND strength > 0.05""",
        (decay_amount, cutoff)
    )
    await db.commit()


# ============================================================
# 群聊梗 (group_memes)
# ============================================================

async def save_group_meme(
    group_id: str,
    meme_type: str,
    content: str,
    trigger_keywords: str = "",
    creator_id: str = "",
    frequency: float = 0.3,
):
    """保存一个群聊梗。

    meme_type: joke / catchphrase / event_reference / code_word
    """
    db = await get_db()
    now = time.time()
    try:
        await db.execute(
            """INSERT INTO group_memes
               (group_id, meme_type, content, trigger_keywords, creator_id,
                frequency, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(group_id, meme_type, content)
               DO UPDATE SET frequency = MIN(0.8, frequency + 0.05), last_used = ?""",
            (str(group_id), meme_type, content[:100], trigger_keywords[:200],
             str(creator_id), frequency, now, now)
        )
        await db.commit()
        logger.info(f"[群聊梗] 保存: group={group_id[:6]} type={meme_type} content={content[:20]}")
    except Exception as e:
        logger.debug(f"[群聊梗] 保存失败: {e}")


async def get_group_memes(group_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """获取群聊梗列表。"""
    db = await get_db()
    async with db.execute(
        """SELECT id, meme_type, content, trigger_keywords, creator_id,
                  frequency, usage_count, created_at, last_used
           FROM group_memes WHERE group_id = ?
           ORDER BY usage_count DESC, frequency DESC LIMIT ?""",
        (str(group_id), limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def find_matching_group_meme(group_id: str, current_msg: str) -> Optional[Dict[str, Any]]:
    """根据当前消息匹配群聊梗。"""
    db = await get_db()
    now = time.time()

    async with db.execute(
        """SELECT id, meme_type, content, trigger_keywords, frequency, last_used
           FROM group_memes WHERE group_id = ?
           ORDER BY frequency DESC""",
        (str(group_id),)
    ) as cursor:
        rows = await cursor.fetchall()

    if not rows:
        return None

    for row in rows:
        keywords = row["trigger_keywords"] or ""
        if not keywords:
            continue
        keyword_list = [k.strip() for k in keywords.split(",") if k.strip()]
        if not any(kw in current_msg for kw in keyword_list):
            continue
        # 冷却：1 小时
        if row["last_used"] and (now - row["last_used"]) < 3600:
            continue
        if random.random() < row["frequency"]:
            await db.execute(
                "UPDATE group_memes SET usage_count = usage_count + 1, last_used = ? WHERE id = ?",
                (now, row["id"])
            )
            await db.commit()
            return dict(row)

    return None


async def get_group_meme_hint(group_id: str, current_msg: str) -> Optional[str]:
    """获取群聊梗提示，供 prompt 注入。"""
    meme = await find_matching_group_meme(group_id, current_msg)
    if not meme:
        return None
    content = meme["content"]
    meme_type = meme["meme_type"]
    type_hints = {
        "joke": f"群里有个梗「{content}」",
        "catchphrase": f"群里有句口头禅「{content}」",
        "event_reference": f"群里发生过一件事「{content}」",
        "code_word": f"群里有个暗号「{content}」",
    }
    return type_hints.get(meme_type, f"群里的梗「{content}」。在合适的时候自然地用出来。")


# ============================================================
# 社交记忆 (social_references)
# ============================================================

async def record_social_reference(
    user_id: str,
    person_name: str,
    relationship: str = "",
    context: str = "",
):
    """记录用户提到的社交圈人物。

    relationship: 朋友/家人/同事/同学/室友/对象 等
    """
    db = await get_db()
    now = time.time()
    try:
        await db.execute(
            """INSERT INTO social_references
               (user_id, person_name, relationship, context, created_at, last_mentioned)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, person_name)
               DO UPDATE SET mentioned_count = mentioned_count + 1,
                             last_mentioned = ?,
                             relationship = COALESCE(NULLIF(?, ''), relationship)""",
            (str(user_id), person_name[:20], relationship[:20], context[:200],
             now, now, now, relationship[:20])
        )
        await db.commit()
        logger.debug(f"[社交记忆] 记录: user={user_id[:6]} person={person_name} rel={relationship}")
    except Exception as e:
        logger.debug(f"[社交记忆] 记录失败: {e}")


async def get_social_references(user_id: str, limit: int = 10) -> List[Dict[str, Any]]:
    """获取用户提到的社交圈人物。"""
    db = await get_db()
    async with db.execute(
        """SELECT person_name, relationship, mentioned_count, context, last_mentioned
           FROM social_references WHERE user_id = ?
           ORDER BY mentioned_count DESC, last_mentioned DESC LIMIT ?""",
        (str(user_id), limit)
    ) as cursor:
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]


async def get_social_reference_hint(user_id: str) -> Optional[str]:
    """获取社交记忆提示，供 prompt 注入。"""
    refs = await get_social_references(user_id, limit=5)
    if not refs:
        return None
    parts = []
    for ref in refs:
        name = ref["person_name"]
        rel = ref.get("relationship", "")
        count = ref.get("mentioned_count", 1)
        if rel:
            parts.append(f"{name}（他的{rel}）")
        elif count >= 3:
            parts.append(f"{name}（经常提到）")
        else:
            parts.append(name)
    if parts:
        return f"他的社交圈：{'、'.join(parts[:5])}。聊天时可以自然地提到。"
    return None


# ============================================================
# 群聊角色定位
# ============================================================

async def get_group_role_hint(group_id: str, bot_member_count: int = 0) -> str:
    """根据群聊情况决定 bot 的角色定位。

    - 人少（<10）：活跃分子，可以多插话
    - 人多（>=10）：安静观察者，少说话
    - 有熟人：更活跃
    - 全是生人：更谨慎
    """
    from .db_group import get_active_members
    active = await get_active_members(group_id, hours=72)

    # 统计关系
    friend_count = 0
    for member in active:
        rels = await get_relationships(group_id, member["member_id"])
        for rel in rels:
            if rel["rel_type"] in ("friend", "close", "teammate") and rel["strength"] >= 0.2:
                friend_count += 1
                break

    total_active = len(active)

    if total_active <= 3:
        return "群里人很少，你可以活跃一点，多参与聊天。"
    elif total_active <= 8:
        if friend_count >= 2:
            return "群里有几个你认识的人，可以自然地参与聊天。"
        return "群里人不多，可以适当参与，但不要抢话。"
    elif total_active <= 15:
        if friend_count >= 3:
            return "群里人不少但有熟人，可以偶尔插话。"
        return "群里人比较多，安静观察为主，被@或有明确话题时再说话。"
    else:
        return "群里很热闹，你主要当观众，只有被@或特别有趣的话题才参与。"
