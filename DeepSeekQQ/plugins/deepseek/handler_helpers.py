"""handler 辅助函数 — 引用决策、问候检测、消息分析、情绪参数、消息分级、已读不回感知。"""
import random
import re
import time
from typing import Optional

from nonebot.adapters.onebot.v11 import GroupMessageEvent
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageEvent
from nonebot.adapters.onebot.v11 import MessageSegment

# ============================================================
# 引用回复消息构造
# ============================================================

def make_reply(event: MessageEvent, msg: Message) -> Message:
    """发送消息（不加引用回复）。"""
    return msg


def make_quote_reply(event: MessageEvent, msg: Message) -> Message:
    """给主回复加引用。"""
    return Message(MessageSegment.reply(event.message_id)) + msg


# ============================================================
# 消息分析
# ============================================================

def is_multi_topic(msg: str) -> bool:
    """判断消息是否包含多个独立话题。"""
    segments = re.split(r'[。！？\n]+', msg)
    meaningful = [s.strip() for s in segments if len(s.strip()) >= 4]
    return len(meaningful) >= 2


def is_question(msg: str) -> bool:
    """判断消息是否是明确的提问。"""
    if msg.rstrip().endswith(('?', '？')):
        return True
    q_keywords = ["怎么", "为什么", "什么", "哪里", "哪个", "多少", "几",
                  "吗", "呢", "能不能", "可以吗", "好不好", "是不是", "有没有"]
    return any(kw in msg for kw in q_keywords)


def is_greeting(msg: str) -> bool:
    """判断消息是否是简单寒暄。"""
    greetings = ["嗯", "嗯嗯", "哈哈", "哦", "好的", "好吧", "行", "可以",
                 "ok", "OK", "收到", "了解", "知道了", "嗯好", "好嘞", "棒"]
    return msg.strip() in greetings


def detect_greeting_type(msg: str, recent_memories: list = None) -> Optional[str]:
    """检测问候类型，返回 'morning'/'night'/'night_uncertain'/None。

    增强版：结合上下文和时间判断晚安置信度。
    - 'night': 高置信度道别（晚安、睡了、明天见）
    - 'night_uncertain': 低置信度（困了、好困，可能是抱怨）
    """
    from datetime import datetime

    # 早安关键词：需要独立成词，避免 "不想起床" 被误判
    morning_kw = ["早安", "早上好", "早呀", "早啊", "good morning"]
    if any(kw in msg for kw in morning_kw):
        return "morning"
    # "早" 单独出现作为早安（但排除 "早就不"、"早就"、"很早" 等）
    if re.search(r'(?<![不就很])(?:^|[^\w])早(?:$|[^\w])', msg):
        return "morning"
    # "起床" 需要是正面语义（排除 "不想起床"、"起不来"、"起晚了"）
    if "起床" in msg and not any(neg in msg for neg in ["不想起床", "起不来", "起晚了", "赖床", "没起"]):
        return "morning"

    # 高置信度晚安关键词
    night_high = ["晚安", "晚安安", "good night", "明天见", "拜拜", "睡了", "去睡了", "睡觉了"]
    if any(kw in msg for kw in night_high):
        return "night"

    # 低置信度关键词：需要上下文或时间辅助判断
    # 注意："累了"/"好累" 很容易是抱怨而非道别，移到更保守的判断逻辑
    night_low = ["困了", "好困", "要睡了", "撑不住了"]
    night_ambiguous = ["下了", "睡觉", "累了", "好累"]
    hour = datetime.now().hour

    if any(kw in msg for kw in night_low):
        # 深夜（22:00-06:00）+ 模糊意图 → 高置信度晚安
        if hour >= 22 or hour < 6:
            return "night"

        # 上下文辅助判断
        if recent_memories and len(recent_memories) >= 2:
            # 检查最近消息：如果用户刚发了很多消息，可能只是抱怨
            recent_user_msgs = [m for m in recent_memories[-6:] if m.get("role") == "user"]
            if len(recent_user_msgs) >= 3:
                return "night_uncertain"
        return "night"

    # 模糊关键词：只在深夜且上下文支持时判为晚安
    if any(kw in msg for kw in night_ambiguous):
        if hour >= 23 or hour < 3:
            return "night"
        # 非深夜：大概率是抱怨，降级为 uncertain
        return "night_uncertain"

    # 深夜 + 短消息（可能只是敷衍）→ 低置信度晚安
    if (hour >= 23 or hour < 3) and len(msg.strip()) <= 3:
        if any(kw in msg for kw in ["嗯", "哦", "好", "行", "88", "拜"]):
            return "night_uncertain"

    return None


def is_night_farewell(msg: str, recent_memories: list = None) -> dict:
    """判断是否是真正的晚安道别。

    Returns:
        {"is_farewell": bool, "confidence": float, "reason": str}
    """
    greeting_type = detect_greeting_type(msg, recent_memories)

    if greeting_type == "night":
        return {"is_farewell": True, "confidence": 0.9, "reason": "明确道别关键词"}

    if greeting_type == "night_uncertain":
        return {"is_farewell": False, "confidence": 0.3, "reason": "可能是抱怨，不是真要睡"}

    return {"is_farewell": False, "confidence": 0.0, "reason": "非道别消息"}


def get_morning_time_hint(hour: int) -> str:
    """根据时间返回差异化早安语气提示。"""
    if 6 <= hour < 7:
        return "用户起得很早，语气带点惊讶和关心，比如'这么早？'"
    elif 7 <= hour < 8:
        return "早上时段，语气元气、活泼"
    elif 8 <= hour < 9:
        return "正常早安时间，自然问候"
    elif 9 <= hour < 10:
        return "用户起得比较晚，可以调侃一句，比如'终于醒了？'"
    return ""


def get_night_affection_hint(affection: dict) -> str:
    """根据好感度返回晚安语气提示。"""
    score = affection.get("score", 0) if affection else 0
    if score >= 200:
        return "关系亲密，可以暧昧一点，比如'梦里见~'"
    elif score >= 50:
        return "关系不错，温暖道别"
    return "关系一般，简洁晚安即可"


def has_time_gap(recent_memories: list, threshold: int = 300) -> bool:
    """检查与上一条消息的时间间隔是否超过阈值。"""
    if not recent_memories:
        return False
    last = recent_memories[-1]
    if isinstance(last, dict) and last.get("timestamp"):
        try:
            return (time.time() - float(last["timestamp"])) > threshold
        except (ValueError, TypeError):
            pass
    return False



# ============================================================
# 引用决策
# ============================================================

def is_bot_at(event: MessageEvent, bot_id: str) -> bool:
    """检查消息是否 @ 了机器人。"""
    if not isinstance(event, GroupMessageEvent):
        return False
    for seg in event.message:
        if seg.type == "at" and str(seg.data.get("qq", "")) == str(bot_id):
            return True
    return False


def should_quote(event: MessageEvent, bot_id: str, raw_msg: str,
                 is_group: bool, is_explicit_search: bool,
                 recent_memories: list) -> bool:
    """模拟真人引用决策：只在需要定位上下文时引用。

    - 群聊 → 条件引用
    - 私聊 → 永不引用
    """
    msg = raw_msg.strip()

    # 私聊不引用
    if not is_group:
        return False

    # ===== 群聊：始终引用的场景 =====
    if is_explicit_search:
        return True
    analysis_keywords = ["怎么看", "分析", "评价", "观点", "说说", "讲讲", "详细介绍"]
    if any(kw in msg for kw in analysis_keywords):
        return True

    # ===== 群聊：始终不引用的场景 =====
    if is_greeting(msg):
        return False

    # ===== 群聊：条件引用 =====
    if is_bot_at(event, bot_id):
        return True
    if is_multi_topic(msg):
        return True
    if has_time_gap(recent_memories):
        return True
    if is_question(msg):
        return True
    return False


# ============================================================
# 回复长度解析
# ============================================================

def parse_target_lines(expr: str) -> int:
    """将 '4-5' / '1' / '2-4' 等字符串解析为随机整数。"""
    if "-" in expr:
        parts = expr.split("-", 1)
        try:
            return random.randint(int(parts[0]), int(parts[1]))
        except (ValueError, IndexError):
            return 3
    try:
        return int(expr)
    except ValueError:
        return 3


# ============================================================
# 情绪参数映射（温度/长度/表情包概率）
# ============================================================

def get_emotion_params(emotion) -> dict:
    """根据 EmotionState 的 VA 值返回行为参数。"""
    if emotion is None or emotion.confidence < 0.4:
        return {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"}

    v, a = emotion.valence, emotion.arousal

    if v > 0.5 and a > 0.7:    # 兴奋
        return {"max_tokens": 1800, "temperature": 1.1, "sticker_chance": 0.50, "target_lines": "4-5"}
    elif v > 0.3 and a > 0.5:  # 开心
        return {"max_tokens": 1500, "temperature": 1.0, "sticker_chance": 0.40, "target_lines": "3-4"}
    elif v < -0.5 and a > 0.5: # 生气
        return {"max_tokens": 600, "temperature": 0.6, "sticker_chance": 0.05, "target_lines": "1"}
    elif v < -0.3:             # 难过
        return {"max_tokens": 800, "temperature": 0.7, "sticker_chance": 0.10, "target_lines": "1-2"}
    elif a < 0.3:              # 平静
        return {"max_tokens": 1200, "temperature": 0.8, "sticker_chance": 0.20, "target_lines": "2-3"}
    elif v > 0 and a > 0.5:    # 害羞
        return {"max_tokens": 1000, "temperature": 0.9, "sticker_chance": 0.30, "target_lines": "2"}
    else:                      # 默认
        return {"max_tokens": 1500, "temperature": 0.9, "sticker_chance": 0.25, "target_lines": "2-4"}


# ============================================================
# 消息分级（真人化：简单消息快速响应）
# ============================================================

def classify_message_complexity(raw_msg: str, has_image: bool, has_voice: bool) -> str:
    """判断消息复杂度：simple / normal / complex。

    simple: 短文本（"嗯"、"哈哈"、"好的"）→ 跳过深度分析，快速回复
    normal: 一般消息 → 完整 pipeline
    complex: 图片/长文/明确提问 → 完整 pipeline + 可能搜索
    """
    msg = raw_msg.strip()
    if len(msg) <= 5 and not has_image and not has_voice:
        return "simple"
    if has_image or len(msg) > 50 or any(kw in msg for kw in [
        "详细", "分析", "解释", "为什么", "怎么弄", "帮我", "教我",
        "介绍", "具体", "怎么说", "什么意思",
    ]):
        return "complex"
    return "normal"


# ============================================================
# 已读不回感知
# ============================================================

def build_reply_gap_hint(gap_seconds: float, affection: dict, schedule, bot_mood: str, current_hour: int = -1) -> str:
    """根据 bot 最后回复到用户当前消息的时间间隔，生成已读不回提示。"""
    import random

    gap_min = gap_seconds / 60
    gap_hour = gap_min / 60
    affection_score = affection.get("score", 0) if affection else 0

    # 判断当前时段
    if current_hour < 0:
        from datetime import datetime
        current_hour = datetime.now().hour
    hour = current_hour
    is_late_night = 0 <= hour < 7  # 深夜到清晨，用户可能在睡觉

    # 深夜回来不算"已读不回"，走另一条路
    if is_late_night and gap_hour >= 3:
        if gap_hour >= 8:
            return random.choice([
                "用户可能刚睡醒，不要提间隔太久，自然地打招呼就好",
                "隔了很久才来消息，可能是刚起床，语气自然一些",
            ])
        return ""

    # 短间隔不触发
    if gap_min < 15:
        return ""

    # 根据好感度调整语气
    if affection_score >= 200:
        # 高好感度：撒娇式
        if 15 <= gap_min < 60:
            return random.choice([
                f"用户过了{int(gap_min)}分钟才回复，可以带点撒娇地说等了很久",
                f"过了{int(gap_min)}分钟才回我，稍微表达一下等待的小委屈",
            ])
        elif 1 <= gap_hour < 3:
            return random.choice([
                f"用户{int(gap_hour)}个多小时没回消息了，可以撒娇说\"终于回我了~\"",
                f"等了{int(gap_hour)}个多小时，语气有点委屈但不生气",
                "好久没回消息了，可以假装生气一下但不要太认真",
            ])
        elif 3 <= gap_hour < 8:
            return random.choice([
                "用户好几个小时没理我了，可以小声抱怨一下",
                "等了好几个小时，语气有点小委屈",
            ])
    elif affection_score >= 50:
        # 中好感度：关心式
        if 15 <= gap_min < 60:
            return random.choice([
                f"用户过了{int(gap_min)}分钟才回复，可以自然地接上话题",
                "隔了一会儿才回，语气自然不要刻意提",
            ])
        elif 1 <= gap_hour < 3:
            return random.choice([
                "用户1个多小时没回，可以问一下是不是在忙",
                "隔了挺久才回，自然地聊回来就好",
            ])
        elif 3 <= gap_hour < 8:
            return random.choice([
                "用户好几个小时没回，可以问问去干嘛了",
                "好几个小时没消息了，可以自然地说\"好久不见~\"",
            ])
    else:
        # 低好感度：平淡式
        if 1 <= gap_hour < 3:
            return random.choice([
                "用户隔了比较久才回复，正常接话就好",
                "过了一段时间才回，不用刻意提",
            ])
        elif gap_hour >= 3:
            return random.choice([
                "用户好几个小时没回，简单接话即可",
                "隔了很久，正常回复不要刻意提间隔",
            ])

    return ""
