"""Prompt 构建与回复长度策略模块。"""
import random
import pytz
from datetime import datetime
from typing import List, Dict, Any, Optional

from .share_prompt import format_shares_for_prompt
from .context_analyzer import ContextAnalysis, EmotionState, emotion_to_prompt_hint


def _get_time_context() -> str:
    tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(tz)

    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[now.weekday()]

    hour = now.hour
    if 5 <= hour < 11:
        period = "早上"
    elif 11 <= hour < 13:
        period = "中午"
    elif 13 <= hour < 17:
        period = "下午"
    elif 17 <= hour < 21:
        period = "晚上"
    elif 21 <= hour < 24:
        period = "夜里"
    else:
        period = "凌晨"

    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%Y年%m月%d日")

    return f"今天是{date_str} {weekday}，当前时间是{period} {time_str}（北京时间）。"


def _build_system_prompt(
    affection: Dict[str, Any],
    mood: Dict[str, Any],
    length: Dict[str, Any],
    relevant_memories: List[str] = None,
    recent_shares: List[Dict[str, Any]] = None,
    user_msg: str = "",
    context_analysis: ContextAnalysis = None,
    emotion_state: EmotionState = None,
    search_context: str = "",
    reminder_context: str = "",
    world_context: str = "",
) -> str:
    time_context = _get_time_context()

    core_identity = f'''{time_context}

你是一只猫娘，正在QQ上和人聊天。你有猫耳有尾巴，但你把自己当普通人，从不说自己是AI或虚拟角色。

说话就像真实女生发QQ：短句子、口语化、有停顿感。不要写括号动作、不要旁白、不要像写小说。想说什么直接说。想起之前聊的事就自然带出来，不用说"我记得"。

性格：猫系、会调侃、嘴硬、偶尔撒娇。对熟人软一点，对生人懒一点。'''

    # 状态信息（语气提示，不超过2句）
    state_hints = []

    # 好感度
    if affection["score"] >= 500:
        state_hints.append("你和他很亲密，会撒娇会任性")
    elif affection["score"] >= 200:
        state_hints.append("你对他有好感，愿意聊天")
    elif affection["score"] >= 50:
        state_hints.append("你们开始熟了，偶尔会多聊几句")

    # 情绪（优先VA模型）
    if emotion_state and emotion_state.confidence >= 0.4:
        hint = emotion_to_prompt_hint(emotion_state)
        if hint:
            state_hints.append(hint.rstrip("。"))
    elif mood["score"] > 70:
        state_hints.append("你心情不错，话多一点")
    elif mood["score"] < 30:
        state_hints.append("你心情一般，回复短一点")

    # 上下文提示（自然融入，不单独列区块）
    if context_analysis:
        if context_analysis.referenced_entity:
            state_hints.append(f"他说的「它」指的是{context_analysis.referenced_entity}")
        if not context_analysis.is_topic_continuation and context_analysis.topic_shift_score > 0.6:
            state_hints.append("他换了话题，接新话题聊")

    state_block = "当前状态：" + "，".join(state_hints) + "。" if state_hints else ""

    # 回复长度指示（简化）
    reply_hint = f"回{length['target_lines']}句左右，{length['style']}。"

    # 记忆（自然融入）
    memory_text = ""
    if relevant_memories:
        snippets = relevant_memories[:2]
        if snippets:
            memory_text = "关于他的事：" + "；".join(snippets)

    # 搜索结果
    search_text = search_context if search_context else ""

    # 提醒上下文
    reminder_text = reminder_context if reminder_context else ""

    # 世界感知
    world_text = world_context if world_context else ""

    # 分享内容
    share_text = format_shares_for_prompt(recent_shares, user_msg) if recent_shares else ""

    # 拼接（尽量紧凑，不加多余标题）
    parts = [core_identity]
    if state_block:
        parts.append(state_block)
    parts.append(reply_hint)
    if memory_text:
        parts.append(memory_text)
    if world_text:
        parts.append(world_text)
    if reminder_text:
        parts.append(reminder_text)
    if search_text:
        parts.append(search_text)
    if share_text:
        parts.append(share_text)

    return "\n\n".join(parts)


def estimate_reply_length(user_msg: str, history: List[Dict[str, Any]]) -> Dict[str, Any]:
    msg_len = len(user_msg.strip())
    is_short = msg_len <= 5
    is_question = any(kw in user_msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢"])
    is_emotional = any(kw in user_msg for kw in ["累", "难过", "开心", "喜欢", "讨厌", "想", "爱", "抱", "亲", "哼", "呜", "要"])
    is_continuation = len(history) >= 2 and history[-1]["role"] == "assistant"
    if is_short and not is_continuation:
        target = random.randint(1, 2)
        style = "简短随意"
    elif is_emotional:
        target = random.randint(2, 4)
        style = "情感倾诉"
    elif is_question:
        target = random.randint(2, 3)
        style = "认真回答"
    else:
        target = random.randint(1, 3)
        style = "自然闲聊"
    return {"target_lines": target, "style": style}
