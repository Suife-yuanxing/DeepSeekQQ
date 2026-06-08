"""handler 辅助函数 — 引用决策、问候检测、消息分析。"""
import re
import time
import random
from typing import Optional

from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, Message, MessageSegment


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

    morning_kw = ["早安", "早", "早上好", "早呀", "早啊", "good morning", "起床"]
    if any(kw in msg for kw in morning_kw):
        return "morning"

    # 高置信度晚安关键词
    night_high = ["晚安", "晚安安", "good night", "明天见", "拜拜", "睡了", "去睡了", "睡觉了"]
    if any(kw in msg for kw in night_high):
        return "night"

    # 低置信度关键词：需要上下文或时间辅助判断
    night_low = ["困了", "好困", "要睡了", "下了", "睡觉", "累了", "好累", "撑不住了"]
    hour = datetime.now().hour

    if any(kw in msg for kw in night_low):
        # 深夜（22:00-06:00）+ 模糊意图 → 高置信度晚安
        if hour >= 22 or hour < 6:
            return "night"

        # 上下文辅助判断
        if recent_memories and len(recent_memories) >= 2:
            # 检查最近消息：如果用户刚发了很多消息，可能只是抱怨
            recent_user_msgs = [m for m in recent_memories[-6:] if m.get("role") == "user"]
            # 如果最近5分钟内有3条以上用户消息，说明还在活跃聊天
            if len(recent_user_msgs) >= 3:
                return "night_uncertain"
        return "night"

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


def is_bot_at(event: MessageEvent, bot_id: str) -> bool:
    """检查 bot 是否被 @ 了。"""
    try:
        for seg in event.message:
            if seg.type == "at" and str(seg.data.get("qq", "")) == str(bot_id):
                return True
    except Exception:
        pass
    return False


# ============================================================
# 引用决策
# ============================================================

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
