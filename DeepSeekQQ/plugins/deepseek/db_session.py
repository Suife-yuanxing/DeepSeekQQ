"""session_state + user_profiles 表操作 — 会话状态持久化与用户画像。"""
import asyncio
from datetime import datetime
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .db_core import get_db
from .utils import generate_session_id

# B3: 全局 scratchpad 读写锁，防止快速连续消息时的竞态条件
scratchpad_lock = asyncio.Lock()


# ---------- session_state ----------
async def save_session_state(session_id: str, topic: str = "", emotion: str = "",
                             context_summary: str = "", bot_mood: str = "{}",
                             scratchpad: str = None):
    db = await get_db()
    now = datetime.now().timestamp()
    # BUGFIX: 两条路径均加锁，防止 scratchpad 和非 scratchpad 写入竞态
    async with scratchpad_lock:
        if scratchpad is not None:
            try:
                await db.execute(
                    """INSERT INTO session_state (session_id, last_topic, last_emotion, last_interaction, context_summary, bot_mood_snapshot, scratchpad)
                       VALUES (?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_id) DO UPDATE SET
                       last_topic = ?, last_emotion = ?, last_interaction = ?, context_summary = ?, bot_mood_snapshot = ?, scratchpad = ?""",
                    (session_id, topic, emotion, now, context_summary, bot_mood, scratchpad,
                     topic, emotion, now, context_summary, bot_mood, scratchpad)
                )
            except Exception:
                await db.rollback()
                raise
        else:
            try:
                await db.execute(
                    """INSERT INTO session_state (session_id, last_topic, last_emotion, last_interaction, context_summary, bot_mood_snapshot)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(session_id) DO UPDATE SET
                       last_topic = ?, last_emotion = ?, last_interaction = ?, context_summary = ?, bot_mood_snapshot = ?""",
                    (session_id, topic, emotion, now, context_summary, bot_mood,
                     topic, emotion, now, context_summary, bot_mood)
                )
            except Exception:
                await db.rollback()
                raise
        await db.commit()


async def get_session_state(session_id: str) -> Optional[Dict[str, Any]]:
    db = await get_db()
    async with db.execute(
        "SELECT last_topic, last_emotion, last_interaction, context_summary, bot_mood_snapshot, "
        "COALESCE(scratchpad, '') as scratchpad FROM session_state WHERE session_id = ?",
        (session_id,)
    ) as cursor:
        row = await cursor.fetchone()
        if not row:
            return None
        return {
            "last_topic": row["last_topic"],
            "last_emotion": row["last_emotion"],
            "last_interaction": row["last_interaction"],
            "context_summary": row["context_summary"],
            "bot_mood_snapshot": row["bot_mood_snapshot"],
            "scratchpad": row["scratchpad"],
        }


async def get_active_sessions(hours: float = 24.0) -> List[str]:
    db = await get_db()
    threshold = datetime.now().timestamp() - hours * 3600
    async with db.execute(
        "SELECT session_id FROM session_state WHERE last_interaction > ?",
        (threshold,)
    ) as cursor:
        rows = await cursor.fetchall()
        return [r["session_id"] for r in rows]


async def get_last_conversation_context(user_id: str) -> Optional[Dict[str, Any]]:
    """获取用户最近一次对话的上下文摘要。"""
    from .db_tags import get_relevant_memory_tags
    session_id = generate_session_id("private", user_id)
    try:
        state = await get_session_state(session_id)
        if not state or not state.get("last_topic"):
            return None

        last_interaction = state.get("last_interaction", 0)
        if last_interaction == 0:
            return None

        hours_ago = (datetime.now().timestamp() - last_interaction) / 3600
        if hours_ago > 72:
            return None

        topic = state.get("last_topic", "")
        summary = state.get("context_summary", "")

        tags = []
        try:
            tag_rows = await get_relevant_memory_tags(user_id, limit=3)
            tags = [r["content"] for r in tag_rows if r["tag_type"] in ("preference", "fact")]
        except Exception:
            pass

        return {
            "topic": topic,
            "summary": summary[:150],
            "tags": tags,
            "hours_ago": hours_ago,
        }
    except Exception as e:
        logger.debug(f"[数据库] get_last_conversation_context 失败: {e}")
        return None


# ---------- memory_summaries ----------
async def get_memory_summary(session_id: str) -> Optional[str]:
    db = await get_db()
    async with db.execute("SELECT summary FROM memory_summaries WHERE session_id = ?", (session_id,)) as cursor:
        row = await cursor.fetchone()
        return row["summary"] if row else None


async def append_memory_summary(session_id: str, summary: str):
    db = await get_db()
    now = datetime.now().timestamp()
    try:
        await db.execute(
            """INSERT INTO memory_summaries (session_id, summary, key_moments, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
               summary = summary || ' | ' || ?, updated_at = ?""",
            (session_id, summary, "[]", now, summary, now)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ---------- user_profiles ----------
async def get_or_create_user_profile(user_id: str) -> Dict[str, Any]:
    db = await get_db()
    now = datetime.now().timestamp()
    async with db.execute(
        "SELECT * FROM user_profiles WHERE user_id = ?", (str(user_id),)
    ) as cursor:
        row = await cursor.fetchone()
        if row:
            return dict(row)
    await db.execute(
        "INSERT INTO user_profiles (user_id, first_interaction) VALUES (?, ?)",
        (str(user_id), now)
    )
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {
        "user_id": str(user_id),
        "relationship_style": "neutral",
        "nickname": "",
        "first_interaction": now,
        "total_messages": 0,
        "known_interests": "",
        "bot_self_summary": "",
    }


_USER_PROFILE_COLUMNS = {
    "relationship_style", "nickname", "first_interaction", "total_messages",
    "known_interests", "bot_self_summary",
}


async def update_user_profile(user_id: str, **kwargs):
    if not kwargs:
        return
    # 白名单校验列名，防止 SQL 注入
    invalid = set(kwargs.keys()) - _USER_PROFILE_COLUMNS
    if invalid:
        logger.warning(f"[DB] update_user_profile: 未知列名 {invalid}，已忽略")
        kwargs = {k: v for k, v in kwargs.items() if k in _USER_PROFILE_COLUMNS}
    if not kwargs:
        return
    db = await get_db()
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [str(user_id)]
    try:
        await db.execute(
            f"UPDATE user_profiles SET {sets} WHERE user_id = ?", values
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


async def update_relationship_style(user_id: str, style: str, weight: float = 0.05):
    from .db_preferences import update_user_preference_raw
    try:
        await update_user_preference_raw(user_id, "relationship_style", style, weight)
    except Exception:
        pass


async def get_relationship_style(user_id: str) -> Optional[str]:
    from .db_preferences import get_top_preference
    return await get_top_preference(user_id, "relationship_style")


# ---------- bot 自我披露 ----------
async def get_undisclosed_facts(user_id: str, affection_score: float) -> Optional[Dict[str, Any]]:
    _SELF_DISCLOSURES = {
        "likes_milk_tea": {"affection_min": 0, "text": "超爱喝奶茶，一天不喝浑身难受，但每次都说要戒"},
        "had_a_crush": {"affection_min": 30, "text": "高中暗恋过隔壁班的男生，不过人家都不知道有我这号人哈哈"},
        "scared_of_exams": {"affection_min": 50, "text": "特别怕期末考，每次都要通宵抱佛脚"},
        "homesick_in_rain": {"affection_min": 80, "text": "下雨天特别想家，想妈妈做的糖醋排骨"},
        "lonely_before_you": {"affection_min": 150, "text": "刚来上海的时候挺孤单的，没什么朋友，所以现在特别珍惜能聊得来的人"},
        "cat_named_tuantuan": {"affection_min": 100, "text": "宿舍的猫叫团团，是只布偶猫，超粘人"},
        "dad_is_teacher": {"affection_min": 200, "text": "其实我爸爸是大学计算机老师，所以我从小就知道怎么翻墙上网"},
        "future_dream": {"affection_min": 300, "text": "毕业后想做UI设计师，想去大厂，但感觉好难啊"},
    }
    db = await get_db()
    for key, info in _SELF_DISCLOSURES.items():
        if affection_score < info["affection_min"]:
            continue
        async with db.execute(
            "SELECT 1 FROM bot_disclosures WHERE user_id = ? AND disclosure_key = ?",
            (str(user_id), key)
        ) as cursor:
            if await cursor.fetchone():
                continue
        return {"key": key, "text": info["text"]}
    return None


async def mark_disclosed(user_id: str, disclosure_key: str):
    db = await get_db()
    now = datetime.now().timestamp()
    try:
        await db.execute(
            """INSERT INTO bot_disclosures (user_id, disclosure_key, revealed_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id, disclosure_key) DO UPDATE SET
               reveal_count = reveal_count + 1, revealed_at = ?""",
            (str(user_id), disclosure_key, now, now)
        )
        await db.commit()
    except Exception:
        await db.rollback()
        raise


# ---------- 用户画像总结 ----------

async def sync_known_interests(user_id: str):
    """从 user_preferences 同步兴趣到 user_profiles.known_interests。

    取 top-5 topic_interest 值，按权重排序，写入 known_interests 字段。
    """
    try:
        from .db_preferences import get_user_preferences
        prefs = await get_user_preferences(user_id)
        topic_interests = prefs.get("topic_interest", {})
        if not topic_interests:
            return

        # 按值降序取 top-5
        sorted_interests = sorted(topic_interests.items(), key=lambda x: x[1], reverse=True)[:5]
        interests_text = "、".join(k for k, v in sorted_interests if v >= 0.2)
        if interests_text:
            await update_user_profile(user_id, known_interests=interests_text)
            logger.debug(f"[画像] sync_known_interests: {user_id[:8]} → {interests_text}")
    except Exception as e:
        logger.debug(f"[画像] sync_known_interests 失败: {e}")


async def build_user_profile_summary(user_id: str, force: bool = False) -> Optional[str]:
    """聚合用户记忆标签和偏好，调用 LLM 生成用户画像摘要。

    摘要存入 user_profiles.bot_self_summary，供 prompt 注入。
    非 force 模式下，已有摘要 72 小时内不重复生成。

    Returns:
        生成的摘要文本，或 None（无需更新/失败时）
    """
    try:
        # 检查是否已有足够新的摘要
        if not force:
            profile = await get_or_create_user_profile(user_id)
            existing = profile.get("bot_self_summary", "")
            # 简单策略：有摘要就跳过（避免频繁调 LLM）
            if existing and len(existing) >= 10:
                return None

        # 1. 收集 memory_tags
        from .db_tags import get_all_memory_tags_for_user
        tags = await get_all_memory_tags_for_user(user_id)
        if not tags:
            logger.debug(f"[画像] 用户 {user_id[:8]} 无记忆标签，跳过画像生成")
            return None

        # 分组整理
        preferences = []
        facts = []
        taboos = []
        for t in tags:
            content = t.get("content", "").strip()
            if not content:
                continue
            conf = t.get("confidence", 0)
            if t.get("tag_type") == "preference":
                preferences.append((content, conf))
            elif t.get("tag_type") == "taboo":
                taboos.append((content, conf))
            else:
                facts.append((content, conf))

        # 2. 收集 user_preferences（取 top 兴趣和关系风格）
        from .db_preferences import get_user_preferences
        user_prefs = await get_user_preferences(user_id)

        interests_str = ""
        topic_interests = user_prefs.get("topic_interest", {})
        if topic_interests:
            sorted_topics = sorted(topic_interests.items(), key=lambda x: x[1], reverse=True)[:5]
            interests_str = "、".join(k for k, v in sorted_topics if v >= 0.2)

        relationship_style = ""
        rel_styles = user_prefs.get("relationship_style", {})
        if rel_styles:
            relationship_style = max(rel_styles, key=rel_styles.get)

        # 3. 如果标签太少（<3条），不值得调用 LLM
        total_items = len(preferences) + len(facts) + len(taboos)
        if total_items < 3 and not interests_str:
            logger.debug(f"[画像] 用户 {user_id[:8]} 信息太少（{total_items}条），跳过")
            return None

        # 4. 构建 LLM prompt
        lines = ['念念在和一个QQ好友长期聊天。请根据以下信息，用念念的口吻总结“念念眼中的这个好友”（2-3句话，80字内，像在和朋友聊天时想到的）。']
        if interests_str:
            lines.append(f"经常聊的话题：{interests_str}")
        if relationship_style:
            style_map = {"tsundere": "互相斗嘴的关系", "gentle": "温柔相处的关系", "polite": "礼貌客气的关系"}
            lines.append(f"相处风格：{style_map.get(relationship_style, relationship_style)}")
        if preferences:
            lines.append("记住的偏好：" + "；".join(p[0] for p in preferences[:5]))
        if facts:
            lines.append("知道的事实：" + "；".join(f[0] for f in facts[:5]))
        if taboos:
            lines.append("注意的禁忌：" + "；".join(t[0] for t in taboos[:3]))
        lines.append("只输出总结文本，不要引号。")

        prompt = "\n".join(lines)

        # 5. 调用 LLM
        from .api import call_deepseek_api
        messages = [
            {"role": "system", "content": "你是念念，一个21岁的普通女孩。用自然的口语化方式总结你对朋友的了解。"},
            {"role": "user", "content": prompt},
        ]
        summary = await call_deepseek_api(messages, temperature=0.7, max_tokens=150, task_type="summary")

        summary = summary.strip()[:200]
        if not summary or len(summary) < 10:
            return None

        # 6. 写入数据库
        await update_user_profile(user_id, bot_self_summary=summary)
        logger.info(f"[画像] 生成画像摘要: {user_id[:8]} → {summary[:50]}...")
        return summary

    except Exception as e:
        logger.info(f"[画像] build_user_profile_summary 失败（非关键）: {e}")
        return None
