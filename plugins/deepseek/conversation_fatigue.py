"""对话疲劳感知 — 检测对话何时该自然收尾。

通过分析用户消息模式（长度趋势、收尾词、回复速度、时段）判断对话疲劳等级，
引导 bot 在合适的时机自然结束对话。
"""
import random
import time
from typing import List, Dict, Any, Optional


# 收尾/敷衍关键词
_CLOSING_WORDS = frozenset([
    "嗯", "嗯嗯", "哦", "哦哦", "好的", "好", "好吧", "行", "行吧",
    "可以", "知道了", "了解", "收到", "ok", "OK", "Ok", "好嘞",
    "嗯好", "嗯行", "是的", "对", "对的", "确实",
])

# 强收尾关键词（用户明确想结束）
_STRONG_CLOSING = frozenset([
    "晚安", "睡了", "拜拜", "再见", "先走了", "我先去", "下了",
    "去忙了", "有事先走了", "明天见", "回头聊", "改天聊",
])


def analyze_conversation_fatigue(
    recent_memories: List[Dict[str, Any]],
    current_msg: str,
    schedule=None,
) -> Dict[str, Any]:
    """分析对话疲劳程度。

    Args:
        recent_memories: 最近的消息列表 (role/content/timestamp)
        current_msg: 当前用户消息
        schedule: ScheduleState 作息状态

    Returns:
        {"level": 0-3, "hint": str, "score": float, "signals": dict}
    """
    signals = {}
    score = 0.0

    # 分离用户和 bot 消息
    user_msgs = [m for m in recent_memories if m.get("role") == "user"]
    # 包含当前消息（已保存到 recent_memories）
    # 但 current_msg 可能还没在列表里，手动加入用于分析
    if user_msgs and user_msgs[-1].get("content") != current_msg:
        user_msgs_with_current = user_msgs + [{"role": "user", "content": current_msg, "timestamp": time.time()}]
    else:
        user_msgs_with_current = user_msgs

    # --- 信号 1: 收尾词检测 ---
    closing_score = _detect_closing_words(current_msg, user_msgs_with_current)
    signals["closing_words"] = closing_score
    score += closing_score

    # --- 信号 2: 用户消息变短 ---
    shortening_score = _detect_message_shortening(user_msgs_with_current)
    signals["message_shortening"] = shortening_score
    score += shortening_score

    # --- 信号 3: 深夜时段 ---
    schedule_score = _detect_schedule_fatigue(schedule)
    signals["schedule"] = schedule_score
    score += schedule_score

    # --- 信号 4: 对话轮次多 ---
    round_score = _detect_long_conversation(user_msgs_with_current)
    signals["long_conversation"] = round_score
    score += round_score

    # --- 信号 5: 用户回复变慢 ---
    slowdown_score = _detect_reply_slowdown(user_msgs)
    signals["reply_slowdown"] = slowdown_score
    score += slowdown_score

    # --- 信号 6: 强收尾关键词 ---
    if current_msg.strip() in _STRONG_CLOSING:
        score += 5.0
        signals["strong_closing"] = 5.0

    # 计算等级
    if score >= 7:
        level = 3
    elif score >= 5:
        level = 2
    elif score >= 3:
        level = 1
    else:
        level = 0

    hint = _build_fatigue_hint(level, signals)

    return {
        "level": level,
        "hint": hint,
        "score": round(score, 2),
        "signals": signals,
    }


def _detect_closing_words(current_msg: str, user_msgs: list) -> float:
    """检测收尾/敷衍词。连续使用收尾词说明疲劳。"""
    msg = current_msg.strip()
    if msg not in _CLOSING_WORDS:
        return 0.0

    # 检查最近几条用户消息是否也是收尾词
    recent_user = [m for m in user_msgs[-5:] if m.get("role") == "user"]
    closing_count = sum(1 for m in recent_user if m.get("content", "").strip() in _CLOSING_WORDS)

    if closing_count >= 3:
        return 4.0  # 连续3+条收尾词，强烈信号
    elif closing_count >= 2:
        return 2.5
    else:
        return 1.0


def _detect_message_shortening(user_msgs: list) -> float:
    """检测用户消息是否在变短。"""
    if len(user_msgs) < 6:
        return 0.0

    # 取最近6条用户消息
    recent = user_msgs[-6:]
    recent_lens = [len(m.get("content", "")) for m in recent]

    # 前半 vs 后半
    first_half = recent_lens[:3]
    second_half = recent_lens[3:]

    avg_first = sum(first_half) / len(first_half) if first_half else 0
    avg_second = sum(second_half) / len(second_half) if second_half else 0

    if avg_first == 0:
        return 0.0

    ratio = avg_second / avg_first

    if ratio < 0.3 and avg_second < 10:
        return 2.5  # 长度降到30%以下且绝对值很短
    elif ratio < 0.5 and avg_second < 15:
        return 1.5
    elif ratio < 0.7:
        return 0.5
    return 0.0


def _detect_schedule_fatigue(schedule) -> float:
    """深夜时段天然疲劳。"""
    if schedule is None:
        return 0.0

    period = getattr(schedule, "period", "active")
    if period == "sleeping":
        return 3.0
    elif period == "night_owl":
        hour = time.localtime().tm_hour
        if hour < 2:
            return 2.0
        return 1.0
    return 0.0


def _detect_long_conversation(user_msgs: list) -> float:
    """对话轮次过多。"""
    count = len(user_msgs)
    if count > 25:
        return 2.0
    elif count > 18:
        return 1.0
    elif count > 12:
        return 0.5
    return 0.0


def _detect_reply_slowdown(user_msgs: list) -> float:
    """检测用户回复是否变慢。"""
    if len(user_msgs) < 5:
        return 0.0

    # 计算最近几条的间隔
    timestamps = [m.get("timestamp", 0) for m in user_msgs if m.get("timestamp")]
    if len(timestamps) < 5:
        return 0.0

    recent_ts = timestamps[-5:]
    intervals = [recent_ts[i+1] - recent_ts[i] for i in range(len(recent_ts)-1)]
    intervals = [i for i in intervals if i > 0]  # 过滤无效间隔

    if len(intervals) < 3:
        return 0.0

    first_half = intervals[:len(intervals)//2]
    second_half = intervals[len(intervals)//2:]

    avg_first = sum(first_half) / len(first_half) if first_half else 1
    avg_second = sum(second_half) / len(second_half) if second_half else 1

    if avg_first == 0:
        return 0.0

    ratio = avg_second / avg_first

    if ratio > 3.0:
        return 2.0  # 回复速度降到1/3以下
    elif ratio > 2.0:
        return 1.0
    elif ratio > 1.5:
        return 0.5
    return 0.0


def _build_fatigue_hint(level: int, signals: dict) -> str:
    """根据疲劳等级生成 prompt 提示。"""
    if level == 0:
        return ""

    if level == 1:
        return (
            "用户可能有点聊累了。回复简短一些，不要主动开启新话题或反问。"
            "如果当前话题聊得差不多了，可以自然地不再延伸。"
        )

    if level == 2:
        return random.choice([
            "用户明显在敷衍/想结束了。不要再提问或开启新话题，可以自然收尾。",
            "对话该结束了。用简短的方式回应，不要追问，可以带一句收尾的话。",
            "用户想结束对话了。自然地收尾，比如\"好啦\"、\"那先这样~\"之类的。",
        ])

    # level == 3
    return random.choice([
        "用户明确想结束了。简短回应后加上自然的收尾（如\"早点休息~\"、\"明天再聊~\"），不要再展开新内容。",
        "对话应该结束了。最后一句用温暖的收尾，不要问问题，不要开启话题。",
        "用户要走了。友好地告别，可以说\"好啦好啦，去吧~\"之类的。",
    ])


def get_closing_message(level: int, schedule=None) -> Optional[str]:
    """为强收尾等级生成一条追加的收尾消息。

    只在 level >= 3 时返回消息，level 2 由 LLM 自然收尾。
    """
    if level < 3:
        return None

    period = getattr(schedule, "period", "active") if schedule else "active"
    hour = time.localtime().tm_hour

    # 深夜场景
    if period == "sleeping" or (0 <= hour < 6):
        return random.choice([
            "好啦好啦，早点休息吧~",
            "快去睡啦，明天再聊~",
            "晚安~做个好梦",
            "去休息吧，熬夜对身体不好哦",
        ])

    # 正常时段
    return random.choice([
        "好啦，那你先忙~",
        "嗯嗯，回头再聊~",
        "行嘞，那先这样~",
        "好~有空再聊",
    ])


def should_suppress_followup(level: int) -> bool:
    """疲劳等级 >= 2 时应抑制追问。"""
    return level >= 2
