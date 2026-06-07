"""记忆系统：情感、心情、标签提取、对话压缩。

ECC 风格改造：
- 置信度评分：新标签 0.5，被引用时 +0.1，未引用时每日 -0.02
- 自动清理：置信度 < 0.15 的标签自动删除
- 缓存上限：防止内存泄漏
"""
import asyncio
import re
import json
import random
import time
from datetime import datetime
from typing import List, Dict, Any, Optional

from . import api
from .config import MAX_MEMORY, AFFECTION_LEVELS, COMPRESS_MESSAGE_THRESHOLD, COMPRESS_TOKEN_THRESHOLD
from nonebot import logger
from .database import (
    save_message, get_recent_memories, trim_memories,
    count_memories, get_oldest_memories, get_keep_ids, delete_memories_except,
    get_affection, update_affection,
    get_catgirl_mood, update_catgirl_mood,
    get_memory_summary, append_memory_summary,
    save_memory_tags, get_relevant_memory_tags, boost_memory_tag,
    get_user_mood, update_user_preference, get_top_preference,
    save_reply_quality, get_quality_stats,
    get_session_state, save_session_state,
)
from .context_analyzer import AnalysisResult

# ---------- 记忆冷却控制 ----------
_recently_used_memories: Dict[str, List[str]] = {}  # user_id -> [最近用过的记忆内容]
MEMORY_COOLDOWN_ROUNDS = 3   # 同一记忆至少间隔3轮才再次使用
MAX_MEMORY_PER_REPLY = 1     # 每次回复最多插入1条记忆
_MEMORY_CACHE_MAX_USERS = 200  # 最大缓存用户数，超过时清理最旧的


def _cleanup_memory_cache():
    """清理不活跃用户的记忆冷却缓存，防止内存泄漏。"""
    if len(_recently_used_memories) <= _MEMORY_CACHE_MAX_USERS:
        return
    keys = list(_recently_used_memories.keys())
    for k in keys[:len(keys) // 2]:
        del _recently_used_memories[k]


async def save_and_get_context(session_id: str, user_id: str, raw_msg: str,
                               analysis: AnalysisResult = None) -> tuple:
    """保存用户消息，返回最近记忆 + 相关标签 + 情感信息。"""
    await save_message(session_id, "user", raw_msg)
    recent = await get_recent_memories(session_id, MAX_MEMORY)
    tags = await _get_relevant_memories(user_id, session_id, raw_msg)
    affection = await get_affection(user_id)

    if analysis and analysis.emotion.confidence >= 0.4:
        from .context_analyzer import emotion_to_mood_label
        mood = emotion_to_mood_label(analysis.emotion)
        await update_catgirl_mood(raw_msg)
    else:
        mood = await update_catgirl_mood(raw_msg)

    return recent, tags, affection, mood


async def save_and_get_context_with_history(session_id: str, user_id: str, raw_msg: str) -> tuple:
    """保存用户消息并返回历史（用于分析器）。"""
    await save_message(session_id, "user", raw_msg)
    recent = await get_recent_memories(session_id, MAX_MEMORY)
    tags = await _get_relevant_memories(user_id, session_id, raw_msg)
    affection = await get_affection(user_id)
    mood = await update_catgirl_mood(raw_msg)

    history_for_analysis = [
        {"role": m["role"], "content": m["content"][:200]}
        for m in recent[:-1]
    ][-6:]

    return recent, tags, affection, mood, history_for_analysis


async def save_reply(session_id: str, user_id: str, raw_msg: str, reply_text: str):
    """保存助手回复，并异步提取记忆标签。"""
    await save_message(session_id, "assistant", reply_text)
    await trim_memories(session_id, MAX_MEMORY)
    asyncio.create_task(_extract_memory_tags(user_id, session_id, raw_msg, reply_text))
    # 功能③：异步学习用户偏好
    asyncio.create_task(_learn_preferences(user_id, raw_msg, reply_text, session_id))
    # 功能⑦：异步评估回复质量
    asyncio.create_task(_evaluate_reply_quality(user_id, session_id, raw_msg, reply_text))
    # 跨会话状态更新
    asyncio.create_task(_update_session_state(session_id, raw_msg, reply_text))
    # 记忆系统深化：提取共同回忆和私人梗
    asyncio.create_task(_extract_shared_memories(user_id, raw_msg, reply_text))
    asyncio.create_task(_extract_private_memes(user_id, raw_msg, reply_text))
    asyncio.create_task(_extract_important_dates(user_id, raw_msg))
    # 社交能力增强：提取社交关系和群聊梗
    asyncio.create_task(_extract_social_references(user_id, raw_msg))
    asyncio.create_task(_extract_group_memes(session_id, user_id, raw_msg, reply_text))
    # 策略性压缩：基于消息数或估算 token 数触发
    msg_count = await count_memories(session_id)
    if msg_count >= COMPRESS_MESSAGE_THRESHOLD:
        asyncio.create_task(_summarize_and_compress(session_id))
    elif msg_count >= 15:
        # 估算 token 数：粗略按字符数 / 1.5
        recent = await get_recent_memories(session_id, 15)
        est_tokens = sum(len(m["content"]) for m in recent) // 1.5
        if est_tokens > COMPRESS_TOKEN_THRESHOLD:
            asyncio.create_task(_summarize_and_compress(session_id))


def _is_memory_relevant(memory_content: str, user_msg: str) -> bool:
    """判断记忆是否与当前用户消息相关。"""
    user_keywords = set(re.findall(r'[一-龥]{2,6}', user_msg))
    if not user_keywords:
        return False
    for kw in user_keywords:
        if kw in memory_content:
            return True
    mem_keywords = set(re.findall(r'[一-龥]{2,6}', memory_content))
    for kw in mem_keywords:
        if kw in user_msg:
            return True
    return False


async def _get_relevant_memories(user_id: str, session_id: str, current_msg: str, limit: int = 5) -> List[str]:
    """获取相关记忆提示语。置信度 + 冷却 + 相关性过滤。"""
    _cleanup_memory_cache()
    try:
        now = datetime.now().timestamp()
        rows = await get_relevant_memory_tags(user_id, limit)

        cooldown_list = _recently_used_memories.get(user_id, [])

        candidates = []
        for row in rows:
            content = row["content"]
            # 冷却检查
            if content in cooldown_list:
                continue
            # 时间衰减：超过14天且置信度低的不使用
            days_ago = (now - row["last_used"]) / 86400
            if days_ago > 14 and row.get("confidence", 0.5) < 0.3:
                continue
            # 相关性检查
            if not _is_memory_relevant(content, current_msg):
                continue
            candidates.append((content, row.get("confidence", 0.5)))

        # 按置信度加权随机选择
        if candidates:
            # 置信度高的更容易被选中
            weights = [max(0.1, c) for _, c in candidates]
            total = sum(weights)
            probs = [w / total for w in weights]
            idx = random.choices(range(len(candidates)), weights=probs, k=1)[0]
            selected_content = candidates[idx][0]

            # 记录冷却
            if user_id not in _recently_used_memories:
                _recently_used_memories[user_id] = []
            _recently_used_memories[user_id].append(selected_content)
            _recently_used_memories[user_id] = _recently_used_memories[user_id][-MEMORY_COOLDOWN_ROUNDS:]

            # 提升被引用标签的置信度
            asyncio.create_task(boost_memory_tag(user_id, selected_content))

            return [f"[{selected_content}]"]

        # 摘要记忆 fallback
        summary = await get_memory_summary(session_id)
        if summary:
            summary_keywords = set(re.findall(r'[一-龥]{2,}', summary))
            user_keywords = set(re.findall(r'[一-龥]{2,}', current_msg))
            has_overlap = bool(summary_keywords & user_keywords)
            if has_overlap or random.random() < 0.8:
                return [f"[之前聊过的：{summary[:150]}]"]

        return []
    except Exception as e:
        logger.error(f"[记忆] 检索失败: {e}")
        return []


async def _summarize_and_compress(session_id: str):
    """对话压缩：将旧消息摘要后存入 summary 表，并删除旧记录。"""
    cnt = await count_memories(session_id)
    if cnt < 25:
        return

    # 摘要缓存：避免重复压缩
    from .context_optimizer import get_cached_summary, set_cached_summary
    cached = get_cached_summary(session_id, cnt)
    if cached:
        logger.debug(f"[记忆] 使用缓存摘要: {session_id[:20]}...")
        keep_ids = await get_keep_ids(session_id, 20)
        await delete_memories_except(session_id, keep_ids)
        return

    old_rows = await get_oldest_memories(session_id, 15)
    if len(old_rows) < 10:
        return

    dialog = "\n".join([f"{r['role']}：{r['content'][:100]}" for r in old_rows])
    prompt = f"""请用结构化方式总结以下对话。输出JSON格式，包含3个字段：
- topic: 当前话题（10字内）
- summary: 核心内容摘要（50字内）
- key_info: 用户提到的关键信息（列表，最多3条）

对话内容：
{dialog}

只输出JSON，不要其他文字。"""
    messages = [
        {"role": "system", "content": "你是一个对话摘要助手，只输出摘要文本，不要任何其他内容。"},
        {"role": "user", "content": prompt}
    ]
    summary = await api.call_deepseek_api(messages, temperature=0.5, task_type="summary")
    summary = summary.strip()[:300]
    # 尝试解析结构化摘要，失败则用原文
    try:
        import json as _json
        clean = re.sub(r"```json\s*|\s*```", "", summary).strip()
        parsed = _json.loads(clean)
        if isinstance(parsed, dict):
            structured = f"话题:{parsed.get('topic','')}; {parsed.get('summary','')}"
            if parsed.get("key_info"):
                structured += f" [关键:{','.join(parsed['key_info'][:3])}]"
            summary = structured[:300]
    except Exception:
        pass  # 用原文
    await append_memory_summary(session_id, summary)

    # 更新摘要缓存
    set_cached_summary(session_id, summary, cnt)

    keep_ids = await get_keep_ids(session_id, 20)
    await delete_memories_except(session_id, keep_ids)
    logger.info(f"[记忆] 会话 {session_id} 已压缩，摘要：{summary[:60]}...")


async def _extract_memory_tags(user_id: str, session_id: str, user_msg: str, reply_text: str):
    """从对话中提取用户标签。新标签初始置信度 0.5。"""
    # Phase 4：群聊也提取记忆标签（范围限定为关键事实）
    is_group = isinstance(session_id, str) and session_id.startswith("group_")
    if is_group:
        # 群聊只提取明确的关键事实（避免噪音）
        if not any(k in user_msg + reply_text for k in ["喜欢", "讨厌", "怕", "不吃", "名字", "生日", "住", "工作", "专业", "养了", "我是"]):
            return

    prompt = f"""从以下对话中，提取关于用户的客观关键信息（偏好、事实、禁忌、情绪）。
只输出 JSON 数组，不要有任何其他文字。没有就输出空数组 []。

用户说：{user_msg}
你回复：{reply_text}

示例输出：
[
  {{"type": "preference", "content": "用户喜欢喝冰美式"}},
  {{"type": "fact", "content": "用户养了一只叫橘子的猫"}},
  {{"type": "taboo", "content": "用户讨厌被叫全名"}}
]"""
    try:
        messages = [
            {"role": "system", "content": "你是一个对话记忆提取助手，只输出JSON数组。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        tags = json.loads(clean)
        if not isinstance(tags, list):
            return
        await save_memory_tags(user_id, tags)
        logger.info(f"[记忆] 提取并保存了 {len(tags)} 条标签")
    except Exception as e:
        logger.info(f"[记忆] 提取失败（非关键错误）: {e}")


async def apply_affection_delta(user_id: str, raw_msg: str):
    """根据消息内容计算情感变化并更新。"""
    sad = ["累", "难过", "伤心", "哭", "烦", "滚", "讨厌", "傻", "笨", "坏", "丑"]
    happy = ["开心", "喜欢", "爱", "棒", "可爱", "喵", "亲", "抱", "摸摸", "乖", "嘿嘿", "哈哈", "想", "好"]
    if any(w in raw_msg for w in sad):
        delta = random.uniform(-1.5, -0.5)
    elif any(w in raw_msg for w in happy):
        delta = random.uniform(1.0, 2.5)
    else:
        delta = random.uniform(0.5, 1.5)
    await update_affection(user_id, delta=delta)


# ---------- 用户偏好自学习（功能③）----------

async def _learn_preferences(user_id: str, raw_msg: str, reply_text: str, session_id: str):
    """从对话行为中异步学习用户偏好。"""
    try:
        # 0. 关系风格学习（Phase 4）：检测用户的口吻风格
        teasing_kw = ["笨蛋", "傻猫", "憨憨", "你不行", "就这", "菜", "笨猫", "蠢猫", "废物", "垃圾"]
        polite_kw = ["请问", "谢谢", "麻烦", "帮忙", "辛苦了", "谢谢你", "多谢"]
        gentle_kw = ["乖", "摸摸", "好喵", "可爱", "最喜欢你了", "抱抱"]

        if any(kw in raw_msg for kw in teasing_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "tsundere", 0.05)
        if any(kw in raw_msg for kw in polite_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "polite", 0.05)
        if any(kw in raw_msg for kw in gentle_kw):
            from .database import update_relationship_style
            await update_relationship_style(user_id, "gentle", 0.05)

        # 1. 回复长度偏好：用户追问 = 想要更长回复
        if len(reply_text) < 30 and any(kw in raw_msg for kw in ["然后呢", "继续", "详细说", "说清楚", "没说完"]):
            await update_user_preference(user_id, "reply_length", "long", 0.1)
        # 回复后用户简短回应 = 想要更短回复
        elif len(reply_text) > 100 and len(raw_msg.strip()) <= 3:
            await update_user_preference(user_id, "reply_length", "short", 0.05)

        # 2. 表情包偏好：用户发表情 = 喜欢表情包
        from nonebot.adapters.onebot.v11 import MessageEvent
        try:
            # 检查用户消息中是否有 face/表情 segment
            if any(kw in raw_msg for kw in ["[表情]", "😂", "🤣", "😍", "😘", "😋", "😜"]):
                await update_user_preference(user_id, "sticker_freq", "high", 0.05)
        except Exception:
            pass

        # 3. 活跃时段
        from datetime import datetime
        hour = datetime.now().hour
        if 6 <= hour < 12:
            period = "morning"
        elif 12 <= hour < 18:
            period = "afternoon"
        elif 18 <= hour < 22:
            period = "evening"
        else:
            period = "night"
        await update_user_preference(user_id, "active_hours", period, 0.1)

        # 4. 话题兴趣：关键词提取
        topic_keywords = {
            "游戏": ["游戏", "打", "玩", "排位", "段位", "王者", "原神", "LOL", "吃鸡"],
            "音乐": ["歌", "音乐", "听", "唱", "专辑", "演唱会"],
            "美食": ["吃", "饭", "美食", "好饿", "外卖", "做饭"],
            "学习": ["作业", "考试", "学习", "课", "大学", "论文"],
            "工作": ["上班", "加班", "工作", "老板", "同事", "工资"],
            "感情": ["喜欢", "恋爱", "对象", "单身", "表白", "分手"],
        }
        for topic, keywords in topic_keywords.items():
            if any(kw in raw_msg for kw in keywords):
                await update_user_preference(user_id, "topic_interest", topic, 0.1)
                break

        # 5. 话题情绪关联：记录用户聊什么话题时的情绪
        from .emotion_deep import record_topic_emotion
        emotion_label = ""
        if any(kw in raw_msg for kw in ["哈哈", "笑", "开心", "好", "棒", "喜欢", "爱"]):
            emotion_label = "开心"
        elif any(kw in raw_msg for kw in ["累", "烦", "难过", "哭", "气"]):
            emotion_label = "难过"
        elif any(kw in raw_msg for kw in ["喜欢", "心动", "想", "爱"]):
            emotion_label = "兴奋"
        if emotion_label:
            for topic, keywords in topic_keywords.items():
                if any(kw in raw_msg for kw in keywords):
                    await record_topic_emotion(user_id, topic, emotion_label)
                    break

        # 6. 昵称学习：用户自定义称呼
        nickname_patterns = [
            r"以后叫我(.{1,6})",
            r"叫我(.{1,6})就行",
            r"叫我(.{1,6})就好",
            r"叫我(.{1,6})吧",
            r"你可以叫我(.{1,6})",
        ]
        import re as _re
        for pattern in nickname_patterns:
            match = _re.search(pattern, raw_msg)
            if match:
                nickname = match.group(1).strip()
                if 1 <= len(nickname) <= 6 and nickname not in ["你", "我", "他"]:
                    from .db_session import update_user_profile
                    await update_user_profile(user_id, nickname=nickname)
                    logger.info(f"[个性化] 用户 {user_id[:6]} 自定义昵称: {nickname}")
                    break

        logger.debug(f"[偏好] 用户 {user_id[:6]} 偏好学习完成")
    except Exception as e:
        logger.info(f"[偏好] 学习失败（非关键）: {e}")


async def get_user_pref_hints(user_id: str) -> Dict[str, str]:
    """获取用户偏好的摘要字典，用于注入 prompt。"""
    try:
        result: Dict[str, str] = {}
        top_length = await get_top_preference(user_id, "reply_length")
        if top_length:
            result["reply_length"] = top_length
        top_sticker = await get_top_preference(user_id, "sticker_freq")
        if top_sticker:
            result["sticker_freq"] = top_sticker
        top_topic = await get_top_preference(user_id, "topic_interest")
        if top_topic:
            result["topic_interest"] = top_topic
        # Phase 4：关系风格
        rel_style = await get_top_preference(user_id, "relationship_style")
        if rel_style:
            result["relationship_style"] = rel_style
        return result
    except Exception:
        return {}


# ---------- 回复质量评估（功能⑦）----------

# 正面/负面反应关键词
_POSITIVE_REACTIONS = ["哈哈", "笑死", "😂", "🤣", "有意思", "好玩", "lol", "牛", "厉害", "太强了", "666"]
_NEGATIVE_REACTIONS = ["？", "什么意思", "没听懂", "啥意思", "说人话", "听不懂"]
_REJECTION_REACTIONS = ["滚", "烦", "不想聊", "闭嘴", "别说了", "无聊"]
_NEUTRAL_REACTIONS = ["哦", "嗯", "好", "行", "知道了", "ok"]


async def _evaluate_reply_quality(user_id: str, session_id: str, raw_msg: str, reply_text: str):
    """评估回复质量：根据用户当前消息判断对上一条回复的反应。"""
    try:
        recent = await get_recent_memories(session_id, 4)
        if len(recent) < 3:
            return

        score = 0.0
        feedback_type = "neutral"

        # 正面反应
        if any(kw in raw_msg for kw in _POSITIVE_REACTIONS):
            score = 1.0
            feedback_type = "emoji_reaction"
        # 困惑反应
        elif any(kw in raw_msg for kw in _NEGATIVE_REACTIONS):
            score = -1.0
            feedback_type = "confusion"
        # 拒绝反应
        elif any(kw in raw_msg for kw in _REJECTION_REACTIONS):
            score = -2.0
            feedback_type = "rejection"
        # 话题延续（用户在继续聊 = 回复引发了兴趣）
        elif len(raw_msg) > 10:
            score = 0.5
            feedback_type = "topic_continuation"
        # 简短中性回应
        elif any(kw in raw_msg for kw in _NEUTRAL_REACTIONS):
            score = 0.0
            feedback_type = "neutral"

        if feedback_type != "neutral":
            await save_reply_quality(
                user_id, session_id, reply_text, score, feedback_type
            )
            logger.debug(f"[质量] user={user_id[:6]} score={score} type={feedback_type}")

        # 每 10 条回复调整一次策略
        stats = await get_quality_stats(user_id, days=7)
        if stats["total"] >= 10 and stats["total"] % 10 == 0:
            await _adjust_reply_strategy(user_id, stats)

    except Exception as e:
        logger.info(f"[质量] 评估失败（非关键）: {e}")


async def _adjust_reply_strategy(user_id: str, stats: Dict[str, Any]):
    """根据历史质量数据调整回复策略。"""
    try:
        avg = stats["avg_score"]
        confusion_rate = stats["confusion_rate"]
        rejection_rate = stats["rejection_rate"]

        # 回复质量差 → 偏好短回复、降低温度
        if avg < -0.3 or rejection_rate > 0.2:
            await update_user_preference(user_id, "reply_length", "short", 0.15)
            logger.info(f"[策略] user={user_id[:6]} 质量差(avg={avg:.2f})，偏好短回复")

        # 回复质量好 → 可以尝试更长回复
        elif avg > 0.5 and stats["positive_rate"] > 0.3:
            await update_user_preference(user_id, "reply_length", "long", 0.1)
            logger.info(f"[策略] user={user_id[:6]} 质量好(avg={avg:.2f})，偏好长回复")

        # 困惑率高 → 偏好更确定的回复（短回复更清晰）
        if confusion_rate > 0.3:
            await update_user_preference(user_id, "reply_length", "short", 0.1)

    except Exception as e:
        logger.info(f"[策略] 调整失败（非关键）: {e}")


# ---------- 跨会话上下文恢复 ----------

async def recover_session_context(session_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """在会话首条消息时，从 session_state 恢复上次对话的上下文。

    只在会话「新鲜」时触发（当前会话历史为空或仅 1 条）。
    返回一个包含 recall_prompt 的字典，用于注入 system prompt。
    """
    try:
        state = await get_session_state(session_id)
        if not state:
            return None

        # 检查当前会话是否「新鲜」（刚启动，还没有历史）
        recent = await get_recent_memories(session_id, 3)
        if len(recent) > 1:
            return None  # 会话已有活跃对话，不需要恢复

        last_interaction = state.get("last_interaction", 0)
        if last_interaction == 0:
            return None

        hours_ago = (time.time() - last_interaction) / 3600
        topic = state.get("last_topic", "")
        emotion = state.get("last_emotion", "")

        # 超过 30 天不恢复（太久远了）
        if hours_ago > 720:
            return None

        # 构建自然的时间描述
        if hours_ago < 1:
            time_hint = "刚才"
        elif hours_ago < 8:
            time_hint = f"{int(hours_ago)}小时前"
        elif hours_ago < 24:
            time_hint = "昨天"
        elif hours_ago < 48:
            time_hint = "前天"
        else:
            time_hint = f"{int(hours_ago / 24)}天前"

        recall_prompt = ""
        if topic:
            recall_prompt = (
                f"你{time_hint}和他在聊「{topic}」，"
                f"当时他{emotion}。" if emotion else
                f"你{time_hint}和他在聊「{topic}」。"
            )
            recall_prompt += (
                "如果他现在说的话和之前有关，自然地接上话题——"
                "不用说「上次聊到」「之前说过」之类的废话，就像一直在聊一样自然接话。"
            )

        # 检查情绪快照（情绪记忆功能）
        mood_care_hint = None
        try:
            from .db_mood import get_last_mood_snapshot, get_mood_care_hint
            snapshot = await get_last_mood_snapshot(user_id)
            if snapshot:
                mood_care_hint = get_mood_care_hint(snapshot)
        except Exception:
            pass

        logger.info(f"[会话恢复] {session_id[:20]}... 上次: {topic[:30] if topic else '无'} ({time_hint})")
        result = {
            "last_topic": topic,
            "last_emotion": emotion,
            "time_hint": time_hint,
            "recall_prompt": recall_prompt,
        }
        if mood_care_hint:
            result["mood_care_hint"] = mood_care_hint
        return result
    except Exception as e:
        logger.info(f"[会话恢复] 失败（非关键）: {e}")
        return None


# ---------- 记忆系统深化：共同回忆 ----------

async def _extract_shared_memories(user_id: str, user_msg: str, reply_text: str):
    """从对话中提取共同经历，保存为 shared_memories。"""
    try:
        # 快速关键词预筛：没有相关词汇就跳过 LLM 调用
        trigger_kw = ["第一次", "还记得", "上次", "一起", "我们", "那时候", "记得吗",
                       "认识", "开始", "纪念", "难忘", "印象", "经历", "回忆"]
        combined = user_msg + reply_text
        if not any(kw in combined for kw in trigger_kw):
            return

        prompt = f"""分析以下对话，判断是否包含值得长期记住的共同经历/重要时刻。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"event_type": "类型", "event_desc": "简短描述(20字内)", "emotion_tag": "情绪标签"}}

event_type 可选值：
- first_chat: 第一次聊天/认识
- shared_experience: 一起经历的事
- funny_milestone: 有趣的里程碑
- emotional_moment: 情感共鸣时刻
- important_event: 重要事件

没有值得记住的共同经历就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        if clean.lower() in ("null", "none", ""):
            return
        import json as _json
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("event_type"):
            from .db_memories_deep import save_shared_memory
            await save_shared_memory(
                user_id,
                event_type=data["event_type"],
                event_desc=data.get("event_desc", ""),
                emotion_tag=data.get("emotion_tag", ""),
                context=f"用户:{user_msg[:100]}",
            )
    except Exception as e:
        logger.debug(f"[共同回忆] 提取失败（非关键）: {e}")


async def _extract_private_memes(user_id: str, user_msg: str, reply_text: str):
    """检测私人梗形成（专属昵称、重复玩笑、暗号）。"""
    try:
        # 昵称检测：用户给 bot 起别名
        nickname_patterns = [
            r"(?:叫你|以后叫|就叫|给你起个|你的名字叫)[\s]*[「\"']?(.{1,6})[」\"']?",
            r"(.{1,4})(?:猫|喵|酱|子|宝|咪)",
        ]
        for pattern in nickname_patterns:
            match = re.search(pattern, user_msg)
            if match:
                nickname = match.group(1).strip()
                if 1 <= len(nickname) <= 6 and nickname not in ["你", "我", "他", "她"]:
                    from .db_memories_deep import save_private_meme
                    await save_private_meme(
                        user_id, "nickname", nickname,
                        origin_context=user_msg[:100],
                        trigger_keywords=nickname,
                        frequency=0.5,
                    )
                    return

        # 玩笑/梗检测：LLM 辅助
        trigger_kw = ["哈哈", "笑死", "梗", "暗号", "只有我们", "专属", "秘密"]
        if not any(kw in user_msg + reply_text for kw in trigger_kw):
            return

        prompt = f"""分析以下对话，判断是否形成了私人梗/专属笑话/暗号。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"meme_type": "类型", "content": "梗的内容(15字内)", "trigger_keywords": "触发关键词(逗号分隔)"}}

meme_type: joke(笑话) / catchphrase(口头禅) / code_word(暗号)
没有新梗就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        if clean.lower() in ("null", "none", ""):
            return
        import json as _json
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("meme_type"):
            from .db_memories_deep import save_private_meme
            await save_private_meme(
                user_id,
                meme_type=data["meme_type"],
                content=data.get("content", ""),
                origin_context=user_msg[:100],
                trigger_keywords=data.get("trigger_keywords", ""),
            )
    except Exception as e:
        logger.debug(f"[私人梗] 提取失败（非关键）: {e}")


async def _extract_important_dates(user_id: str, user_msg: str):
    """从用户消息中提取重要日期（生日、纪念日等）。"""
    try:
        # 快速关键词预筛
        date_kw = ["生日", "纪念日", "认识", "结婚", "周年", "几月几号", "什么时候"]
        if not any(kw in user_msg for kw in date_kw):
            return

        # 日期格式匹配
        date_patterns = [
            (r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})[日号]?", "full"),
            (r"(\d{1,2})[月/-](\d{1,2})[日号]?", "month_day"),
        ]

        for pattern, fmt in date_patterns:
            match = re.search(pattern, user_msg)
            if match:
                if fmt == "full":
                    date_value = f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
                else:
                    date_value = f"{int(match.group(1)):02d}-{int(match.group(2)):02d}"

                # 判断日期类型
                if "生日" in user_msg:
                    date_type = "birthday"
                    desc = f"生日 {date_value}"
                elif "纪念" in user_msg or "周年" in user_msg:
                    date_type = "anniversary"
                    desc = f"纪念日 {date_value}"
                elif "认识" in user_msg or "第一次" in user_msg:
                    date_type = "first_chat"
                    desc = f"认识的日子 {date_value}"
                else:
                    date_type = "special_day"
                    desc = f"特别的日子 {date_value}"

                from .db_memories_deep import save_important_date
                await save_important_date(
                    user_id, date_type, date_value,
                    description=desc,
                    repeat_yearly=(fmt == "month_day"),
                )
                return

        # 无日期格式但有关键词时，用 LLM 提取
        if "生日" in user_msg and not any(re.search(p, user_msg) for p, _ in date_patterns):
            prompt = f"""用户提到了生日，提取日期信息。只输出 JSON 或 null。

用户说：{user_msg}

如果有日期，输出：{{"date_value": "MM-DD", "description": "描述"}}
没有明确日期就输出 null。只输出JSON。"""
            messages = [
                {"role": "system", "content": "你是一个信息提取助手，只输出JSON或null。"},
                {"role": "user", "content": prompt}
            ]
            raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
            clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
            if clean.lower() not in ("null", "none", ""):
                import json as _json
                data = _json.loads(clean)
                if isinstance(data, dict) and data.get("date_value"):
                    from .db_memories_deep import save_important_date
                    await save_important_date(
                        user_id, "birthday", data["date_value"],
                        description=data.get("description", f"生日 {data['date_value']}"),
                    )
    except Exception as e:
        logger.debug(f"[重要日期] 提取失败（非关键）: {e}")


# ---------- 记忆深化提示生成 ----------

async def get_shared_memory_hint(user_id: str, current_msg: str) -> Optional[str]:
    """获取共同回忆提示，供 prompt 注入。"""
    try:
        from .db_memories_deep import get_recall_candidates
        candidates = await get_recall_candidates(user_id, current_msg, limit=1)
        if not candidates:
            return None
        mem = candidates[0]
        desc = mem["event_desc"]
        event_type = mem["event_type"]
        type_hints = {
            "first_chat": "你们第一次认识的场景",
            "shared_experience": "你们一起经历的事",
            "funny_milestone": "你们之间的趣事",
            "emotional_moment": "你们之间的情感时刻",
            "important_event": "你们共同的重要事件",
        }
        hint = type_hints.get(event_type, "你们的回忆")
        return f"{hint}——{desc}。如果自然的话，可以在对话中不经意提到这段回忆。"
    except Exception:
        return None


async def get_private_meme_hint(user_id: str, current_msg: str) -> Optional[str]:
    """获取私人梗提示，供 prompt 注入。"""
    try:
        from .db_memories_deep import find_matching_meme
        meme = await find_matching_meme(user_id, current_msg)
        if not meme:
            return None
        content = meme["content"]
        meme_type = meme["meme_type"]
        type_hints = {
            "nickname": f"你们之间有个专属昵称「{content}」",
            "joke": f"你们之间有个梗「{content}」",
            "catchphrase": f"你们的口头禅「{content}」",
            "code_word": f"你们的暗号「{content}」",
        }
        return type_hints.get(meme_type, f"你们的默契「{content}」。在合适的时候自然地用出来。")
    except Exception:
        return None


async def get_date_hint(user_id: str) -> Optional[str]:
    """获取重要日期提示，供 prompt 注入。"""
    try:
        from .db_memories_deep import get_today_dates, get_upcoming_dates
        from datetime import datetime
        today = datetime.now().strftime("%m-%d")
        today_dates = await get_today_dates(user_id, today)
        if today_dates:
            date = today_dates[0]
            desc = date.get("description", date["date_type"])
            return f"今天是一个特别的日子——{desc}。可以主动提起，表达在意。"

        upcoming = await get_upcoming_dates(user_id, within_days=3)
        if upcoming:
            date = upcoming[0]
            days = date.get("days_until", 0)
            desc = date.get("description", date["date_type"])
            if days == 1:
                return f"明天是{desc}，可以在聊天中稍微暗示一下你记得。"
            elif days <= 3:
                return f"快到{desc}了（还有{days}天），可以提前准备一下。"
        return None
    except Exception:
        return None


# ---------- 社交能力增强 ----------

async def _extract_social_references(user_id: str, user_msg: str):
    """从用户消息中提取社交圈人物（朋友、家人、同事等）。"""
    try:
        # 关键词预筛
        social_kw = ["我朋友", "我同学", "我同事", "我室友", "我对象", "我男/女朋友",
                      "我妈", "我爸", "我姐", "我哥", "我弟", "我妹",
                      "我老板", "我老师", "我闺蜜", "我兄弟", "我基友",
                      "朋友说", "同学说", "同事说", "室友说"]
        if not any(kw in user_msg for kw in social_kw):
            # 也检查"XX说"模式
            if not re.search(r'[一-鿿]{1,4}(?:说|跟我|找我|约我)', user_msg):
                return

        prompt = f"""从以下用户消息中，提取提到的社交圈人物。
只输出 JSON 数组，没有就输出空数组 []。

用户说：{user_msg}

示例输出：
[
  {{"name": "小明", "relationship": "朋友", "context": "小明说周末一起打球"}},
  {{"name": "妈妈", "relationship": "家人", "context": "我妈让我早点睡"}}
]

只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个信息提取助手，只输出JSON数组。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        refs = json.loads(clean)
        if not isinstance(refs, list):
            return

        from .db_social import record_social_reference
        for ref in refs:
            if isinstance(ref, dict) and ref.get("name"):
                await record_social_reference(
                    user_id,
                    person_name=ref["name"],
                    relationship=ref.get("relationship", ""),
                    context=ref.get("context", user_msg[:100]),
                )
    except Exception as e:
        logger.debug(f"[社交记忆] 提取失败（非关键）: {e}")


async def _extract_group_memes(session_id: str, user_id: str, user_msg: str, reply_text: str):
    """检测群聊梗形成并保存。"""
    try:
        is_group = isinstance(session_id, str) and session_id.startswith("group_")
        if not is_group:
            return

        group_id = session_id.replace("group_", "")

        # 快速关键词预筛
        trigger_kw = ["哈哈", "笑死", "经典", "名场面", "永远的神", "暗号", "只有我们"]
        if not any(kw in user_msg + reply_text for kw in trigger_kw):
            return

        prompt = f"""分析以下群聊对话，判断是否形成了群聊梗/专属笑话。
只输出 JSON，没有就输出 null。

用户说：{user_msg}
回复：{reply_text}

如果有，输出：
{{"meme_type": "类型", "content": "梗的内容(15字内)", "trigger_keywords": "触发关键词(逗号分隔)"}}

meme_type: joke(笑话) / catchphrase(口头禅) / event_reference(事件引用) / code_word(暗号)
没有新梗就输出 null。只输出JSON。"""
        messages = [
            {"role": "system", "content": "你是一个对话分析助手，只输出JSON或null。"},
            {"role": "user", "content": prompt}
        ]
        raw = await api.call_deepseek_api(messages, temperature=0.3, task_type="extract")
        clean = re.sub(r"```json\s*|\s*```", "", raw).strip()
        if clean.lower() in ("null", "none", ""):
            return
        import json as _json
        data = _json.loads(clean)
        if isinstance(data, dict) and data.get("meme_type"):
            from .db_social import save_group_meme
            await save_group_meme(
                group_id,
                meme_type=data["meme_type"],
                content=data.get("content", ""),
                trigger_keywords=data.get("trigger_keywords", ""),
                creator_id=user_id,
            )
    except Exception as e:
        logger.debug(f"[群聊梗] 提取失败（非关键）: {e}")


async def _update_session_state(session_id: str, raw_msg: str, reply_text: str):
    """每次回复后异步更新 session_state，记录当前对话状态。"""
    try:
        # 提取简要话题（用户消息中的前几个中文词）
        topic_keywords = re.findall(r'[一-鿿]{2,8}', raw_msg)
        topic = "、".join(topic_keywords[:3]) if topic_keywords else "闲聊"

        # 提取用户可能的情绪
        emotion = ""
        if any(kw in raw_msg for kw in ["哈哈", "笑", "开心", "好", "棒", "喜欢"]):
            emotion = "心情不错"
        elif any(kw in raw_msg for kw in ["累", "烦", "难过", "哭", "气"]):
            emotion = "情绪不太好"
        elif any(kw in raw_msg for kw in ["？", "吗", "怎么", "为什么"]):
            emotion = "在问问题"

        await save_session_state(
            session_id,
            topic=topic[:30],
            emotion=emotion,
            context_summary=f"用户: {raw_msg[:100]} | 回复: {reply_text[:100]}"
        )
        logger.debug(f"[会话状态] {session_id[:20]}... 已更新: {topic[:20]}")
    except Exception as e:
        logger.debug(f"[会话状态] 更新失败（非关键）: {e}")
