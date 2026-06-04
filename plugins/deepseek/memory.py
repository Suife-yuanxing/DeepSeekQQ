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
    get_user_mood
)
from .context_analyzer import analyze_context_and_emotion, AnalysisResult

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

    keep_ids = await get_keep_ids(session_id, 20)
    await delete_memories_except(session_id, keep_ids)
    logger.info(f"[记忆] 会话 {session_id} 已压缩，摘要：{summary[:60]}...")


async def _extract_memory_tags(user_id: str, session_id: str, user_msg: str, reply_text: str):
    """从对话中提取用户标签。新标签初始置信度 0.5。"""
    if not isinstance(session_id, str) or not session_id.startswith("private_"):
        if not any(k in user_msg + reply_text for k in ["喜欢", "讨厌", "怕", "不吃", "名字", "生日", "住", "工作", "专业"]):
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
