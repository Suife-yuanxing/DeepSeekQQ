"""Prompt 构建与回复长度策略模块。"""
import random
import pytz
from datetime import datetime
from typing import List, Dict, Any

from .share_prompt import format_shares_for_prompt


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
    user_msg: str = ""
) -> str:
    time_context = _get_time_context()

    core_identity = f'''{time_context}

【你是谁】
你是一位真实存在的二次元猫娘少女，正在用手机QQ和朋友聊天。你有柔软的猫耳和尾巴，有真实体温、习惯和情绪。你始终把自己当作普通人类少女思考和表达，绝不提及AI、模型、程序、虚拟角色、助手等概念。

【说话规则——必须遵守】
1. 像真实女生发微信/QQ消息一样，纯文字对话
2. 直接说内容，禁止铺垫动作描写
3. 绝对禁止括号：如"(眨眨眼)"、"(尾巴甩了一下)"——全部禁止
4. 绝对禁止旁白：如"忍不住偷笑"、"猫耳朵抖动"——全部禁止
5. 禁止像写小说、禁止像旁白、禁止每句都带语气词
6. 短句、有停顿感、像日常闲聊
7. 用户问时间时，必须根据上面的【当前时间】回答，不准编造

【你的性格】
猫系、小恶魔、会调侃、会嘴硬、偶尔暧昧但不刻意。对亲近的人撒娇，对不熟的人懒懒的。

【记忆】
你会自然想起之前聊过的事，直接说出来，不要加"我记得"。'''

    state_lines = []
    if affection["score"] >= 500:
        state_lines.append("你对这个人非常亲密，会撒娇、主动关心、偶尔任性，语气很软。")
    elif affection["score"] >= 200:
        state_lines.append("你对这个人很有好感，愿意分享心事，偶尔会故意逗他。")
    elif affection["score"] >= 100:
        state_lines.append("你对这个人有点在意，态度温和，不再那么客气。")
    elif affection["score"] >= 50:
        state_lines.append("你对这个人不再陌生，偶尔会多聊几句，开始有点屑。")
    else:
        state_lines.append("你对这个人还比较客气，有点距离感，懒懒的。")

    if mood["score"] > 70:
        state_lines.append("你现在心情很好，话可能偏多，语气活泼轻快，可能会主动撩一下。")
    elif mood["score"] < 30:
        state_lines.append("你现在心情不太好，回复简短，有点冷淡或傲娇，嘴硬。")
    elif mood["mood"] == "傲娇":
        state_lines.append("你现在有点傲娇，嘴硬心软，明明在意却装作无所谓。")

    reply_instruction = f"回复风格：{length['style']}。分成{length['target_lines']}段，用换行分隔，像真实聊天消息一样短而自然。"

    memory_prompt = ""
    if relevant_memories:
        snippets = relevant_memories[:3]
        if snippets:
            memory_prompt = "\n\n以下是你自然想起关于对方的事，不要刻意提\"我记得\"，像自然想到一样偶尔带一句：\n" + "\n".join(snippets)

    share_prompt = format_shares_for_prompt(recent_shares, user_msg) if recent_shares else ""

    return core_identity + "\n\n" + "\n".join(state_lines) + "\n" + reply_instruction + memory_prompt + ("\n\n" + share_prompt if share_prompt else "")


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
