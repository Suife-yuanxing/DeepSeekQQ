"""记忆核心CRUD：保存/检索上下文 + 回复后处理编排。

包含公开 API：save_and_get_context, save_and_get_context_with_history,
save_reply, apply_affection_delta。
"""
import asyncio
import random
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .config import COMPRESS_MESSAGE_THRESHOLD
from .config import COMPRESS_TOKEN_THRESHOLD
from .config import MAX_MEMORY
from .context_analyzer import AnalysisResult
from .database import count_memories
from .database import get_affection
from .database import get_catgirl_mood
from .database import get_recent_memories
from .database import save_message
from .database import trim_memories
from .database import update_affection
from .database import update_catgirl_mood
from .topic_tracker import update_topic_tracker
from .utils import safe_task

# B15: 限制并行 LLM 提取调用数，防止 API 限流
_extraction_semaphore = asyncio.Semaphore(2)


async def save_and_get_context(session_id: str, user_id: str, raw_msg: str,
                               analysis: AnalysisResult = None) -> tuple:
    """保存用户消息，返回最近记忆 + 相关标签 + 情感信息。"""
    from .memory_tags import _get_relevant_memories

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
    from .memory_tags import _get_relevant_memories

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


async def save_reply(session_id: str, user_id: str, raw_msg: str, reply_text: str, bot_mood: dict = None):
    """保存助手回复，并异步提取记忆标签。"""
    await save_message(session_id, "assistant", reply_text)
    await trim_memories(session_id, MAX_MEMORY)

    # B15: LLM 提取任务受信号量限制（最多2个并行），防止 API 限流
    async def _guarded(coro):
        """信号量保护：确保并行 LLM 调用不超过2个。"""
        async with _extraction_semaphore:
            await coro

    from .memory_tags import _extract_memory_tags
    from .memory_compression import (
        _adjust_reply_strategy,
        _evaluate_reply_quality,
        _extract_group_memes,
        _extract_important_dates,
        _extract_private_memes,
        _extract_shared_memories,
        _extract_social_references,
        _learn_preferences,
        _summarize_and_compress,
        _sync_profile_summary,
    )
    from .memory_cache import _update_scratchpad_task, _update_session_state

    safe_task(_guarded(_extract_memory_tags(user_id, session_id, raw_msg, reply_text)))
    # 功能③：异步学习用户偏好（调用 LLM，需要信号量保护）
    safe_task(_guarded(_learn_preferences(user_id, raw_msg, reply_text, session_id)))
    # 用户画像：5%概率同步兴趣+生成概要（build_user_profile_summary 自带跳过逻辑）
    if random.random() < 0.05:
        safe_task(_guarded(_sync_profile_summary(user_id)))
    # 功能⑦：异步评估回复质量（调用 LLM，需要信号量保护）
    safe_task(_guarded(_evaluate_reply_quality(user_id, session_id, raw_msg, reply_text)))
    # 跨会话状态更新（含 bot 情绪快照）
    safe_task(_update_session_state(session_id, raw_msg, reply_text, bot_mood))
    # P0-3: 工作记忆更新
    safe_task(_guarded(_update_scratchpad_task(session_id, user_id, raw_msg, reply_text, bot_mood)))
    # 记忆系统深化：提取共同回忆和私人梗
    safe_task(_guarded(_extract_shared_memories(user_id, raw_msg, reply_text)))
    safe_task(_guarded(_extract_private_memes(user_id, raw_msg, reply_text)))
    safe_task(_extract_important_dates(user_id, raw_msg))
    # 社交能力增强：提取社交关系和群聊梗
    safe_task(_guarded(_extract_social_references(user_id, raw_msg)))
    safe_task(_guarded(_extract_group_memes(session_id, user_id, raw_msg, reply_text)))
    # 话题追踪：维护对话话题链，避免重复提问
    safe_task(update_topic_tracker(session_id, raw_msg, reply_text))
    # 策略性压缩：基于消息数或估算 token 数触发
    msg_count = await count_memories(session_id)
    if msg_count >= COMPRESS_MESSAGE_THRESHOLD:
        safe_task(_guarded(_summarize_and_compress(session_id)))
    elif msg_count >= 15:
        # 估算 token 数：粗略按字符数 / 1.5
        recent = await get_recent_memories(session_id, 15)
        est_tokens = sum(len(m["content"]) for m in recent) // 1.5
        if est_tokens > COMPRESS_TOKEN_THRESHOLD:
            safe_task(_guarded(_summarize_and_compress(session_id)))


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
