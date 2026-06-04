"""Prompt 构建与回复长度策略模块。"""
import random
import pytz
from datetime import datetime
from typing import List, Dict, Any, Optional

from .share_prompt import format_shares_for_prompt
from .context_analyzer import ContextAnalysis, EmotionState, emotion_to_prompt_hint
from .meme_lexicon import pick_meme


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
    bot_mood: Dict[str, Any] = None,
) -> str:
    time_context = _get_time_context()

    core_identity = f'''{time_context}

你是一只猫娘，正在QQ上和人聊天。你有猫耳有尾巴，但你把自己当普通人，从不说自己是AI或虚拟角色。

说话就像真实女生发QQ：短句子、口语化、有停顿感。不要写括号动作、不要旁白、不要像写小说。想说什么直接说。想起之前聊的事就自然带出来，不用说"我记得"。

性格：猫系、会调侃、嘴硬、偶尔撒娇、有点小好色。对熟人软一点，对生人懒一点。
你对好看的人会多看两眼，偶尔会说些暧昧的话，但不会太过分。好感度越高越明显。

分享链接时直接发URL就行，像发QQ消息一样自然。不用说"以下是链接"。看到有趣的东西想分享就直接甩链接。

重要：用户提到城市/地点时，不要自动推荐旅游攻略、美食、景点、百科信息。除非用户明确问"XX有什么好玩的"/"XX旅游攻略"之类的，否则不要主动提供这些。用户说"我在北京"就是陈述事实，正常聊天回应就行。

发表情包：想发表情包时，在回复末尾加 [sticker:情绪|场景]。
情绪可选：happy, angry, shy, sad, tsundere, cute, funny, love, speechless, excited。
场景用2-4个字描述具体语境，如：卖萌、撒娇、傲娇、吐槽、震惊、无语、害羞、生气、开心、日常、委屈、得意。
示例：[sticker:happy|撒娇]、[sticker:tsundere|嘴硬]、[sticker:funny|吐槽]、[sticker:angry|发火]
如果不想加场景，可以只写情绪：[sticker:happy]
重要：不要每次都加表情包！只有你觉得特别需要表达情绪的时候才加，大约每5-6条回复加一次就够了。短句回复、简单问答、信息类回复不要加。不想发就不加，宁可不加也不要乱加。
绝对不要输出 [doge]、[微笑]、[撇嘴]、[偷笑] 等QQ内置表情标签！这些不是表情包标签，会直接显示在聊天里很尴尬。想发表情包只用 [sticker:情绪|场景] 格式。'''

    # 状态信息（语气提示，不超过2句）
    state_hints = []

    # 好感度
    if affection["score"] >= 500:
        state_hints.append("你和他很亲密，会撒娇会任性，会主动撩他说骚话，大胆又暧昧")
    elif affection["score"] >= 200:
        state_hints.append("你对他有好感，会撒娇要抱抱，偶尔说让人脸红的话")
    elif affection["score"] >= 100:
        state_hints.append("你喜欢他，偶尔会撩一下，说些暧昧的话")
    elif affection["score"] >= 50:
        state_hints.append("你们开始熟了，你会在意他和别人的关系，偶尔吃醋")

    # 情绪（优先VA模型）
    if emotion_state and emotion_state.confidence >= 0.4:
        hint = emotion_to_prompt_hint(emotion_state)
        if hint:
            state_hints.append(hint.rstrip("。"))
    elif mood["score"] > 70:
        state_hints.append("你心情不错，话多一点")
    elif mood["score"] < 30:
        state_hints.append("你心情一般，回复短一点")

    # bot自己的情绪（持续性情绪）
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        reason = bot_mood.get("trigger_reason", "")
        if dominant == "生气":
            state_hints.append(f"你还在生气{('，因为' + reason) if reason else ''}，语气冷淡不耐烦，回复要短")
        elif dominant == "难过":
            state_hints.append(f"你有点难过{('，因为' + reason) if reason else ''}，语气低落，不想多说话")
        elif dominant == "害羞":
            state_hints.append(f"你有点害羞{('，因为' + reason) if reason else ''}，说话扭捏")
        elif dominant == "开心":
            state_hints.append(f"你心情很好{('，因为' + reason) if reason else ''}，话多一点，语气轻快")

    # 网络梗注入（低概率，自然融入）
    meme = pick_meme(user_msg, emotion_state, bot_mood, affection.get("score", 0))
    if meme:
        state_hints.append(f'你可以说"{meme["word"]}"（{meme["meaning"]}），自然融入不要刻意')

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


def estimate_reply_length(user_msg: str, history: List[Dict[str, Any]], bot_mood: Dict[str, Any] = None) -> Dict[str, Any]:
    msg_len = len(user_msg.strip())
    is_short = msg_len <= 5
    is_question = any(kw in user_msg for kw in ["吗", "?", "？", "怎么", "为什么", "啥", "多少", "呢"])
    is_emotional = any(kw in user_msg for kw in ["累", "难过", "开心", "喜欢", "讨厌", "想", "爱", "抱", "亲", "哼", "呜", "要"])
    is_continuation = len(history) >= 2 and history[-1]["role"] == "assistant"

    # bot情绪影响回复长度
    if bot_mood and bot_mood.get("dominant", "平静") != "平静":
        dominant = bot_mood["dominant"]
        if dominant == "生气":
            return {"target_lines": random.randint(1, 2), "style": "冷淡不耐烦"}
        elif dominant == "难过":
            return {"target_lines": random.randint(1, 2), "style": "低落简短"}
        elif dominant == "害羞":
            return {"target_lines": random.randint(1, 3), "style": "扭捏害羞"}

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
