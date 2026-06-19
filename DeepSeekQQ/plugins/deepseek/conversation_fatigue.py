"""对话疲劳感知 — 检测对话何时该自然收尾。

通过分析用户消息模式（长度趋势、收尾词、回复速度、时段）判断对话疲劳等级，
引导 bot 在合适的时机自然结束对话。

真人化 P2-2：引入基线学习——学习每个用户的回复风格基线，
偏离基线才判定疲劳（而非绝对阈值）。区分「忙」（间隔拉长但内容仍丰富）
和「烦」（间隔拉长 + 内容变短 + 无反问）。
"""
import math
import random
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

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

# 真人化 P2-2：基线学习最小样本数
from .config import HUMANIZE_TUNING_BASELINE_MIN_SAMPLES as _MIN_BASELINE_SAMPLES

# 总线疲劳得分中各信号的权重（含相关系数折扣）
# 信号 1 收尾词: 权重 1.0（独立信号，不受相关影响）
# 信号 2 消息变短: 权重 1.0（与信号 5 相关）
# 信号 3 深夜时段: 权重 1.0（独立信号）
# 信号 4 对话轮次: 权重 1.0（独立信号）
# 信号 5 回复变慢: 权重 1.0（与信号 2 相关）
_SIGNAL_WEIGHTS = {
    "closing_words": 1.0,
    "message_shortening": 0.85,  # 折扣：与 reply_slowdown 部分相关
    "schedule": 1.0,
    "long_conversation": 1.0,
    "reply_slowdown": 0.85,      # 折扣：与 message_shortening 部分相关
}

# 回复变短和回复变慢的皮尔逊相关系数（预设，定期更新）
# audit-3-1 修复：双重计数折扣
_R_SHORTENING_SLOWDOWN = 0.3  # 中等正相关


def analyze_conversation_fatigue(
    recent_memories: List[Dict[str, Any]],
    current_msg: str,
    schedule=None,
    user_baseline: dict = None,
) -> Dict[str, Any]:
    """分析对话疲劳程度。

    Args:
        recent_memories: 最近的消息列表 (role/content/timestamp)
        current_msg: 当前用户消息
        schedule: ScheduleState 作息状态
        user_baseline: 用户回复风格基线（真人化 P2-2）{
            "sample_count": int, "avg_reply_length": float,
            "avg_reply_gap": float, "sticker_rate": float,
            "question_rate": float, "last_updated": float
        }

    Returns:
        {"level": 0-3, "hint": str, "score": float, "signals": dict, "fatigue_type": str}
    """
    signals = {}
    score = 0.0

    # 分离用户和 bot 消息
    user_msgs = [m for m in recent_memories if m.get("role") == "user"]
    # 包含当前消息
    if user_msgs and user_msgs[-1].get("content") != current_msg:
        user_msgs_with_current = user_msgs + [{"role": "user", "content": current_msg, "timestamp": time.time()}]
    else:
        user_msgs_with_current = user_msgs

    has_baseline = (user_baseline and user_baseline.get("sample_count", 0) >= _MIN_BASELINE_SAMPLES)

    # --- 信号 1: 收尾词检测 ---
    closing_score = _detect_closing_words(current_msg, user_msgs_with_current)
    signals["closing_words"] = closing_score
    score += closing_score * _SIGNAL_WEIGHTS["closing_words"]

    # --- 信号 2: 用户消息变短 ---
    if has_baseline:
        shortening_score = _detect_shortening_vs_baseline(
            user_msgs_with_current, user_baseline
        )
    else:
        shortening_score = _detect_message_shortening(user_msgs_with_current)
    signals["message_shortening"] = shortening_score
    score += shortening_score * _SIGNAL_WEIGHTS["message_shortening"]

    # --- 信号 3: 深夜时段 ---
    schedule_score = _detect_schedule_fatigue(schedule)
    signals["schedule"] = schedule_score
    score += schedule_score * _SIGNAL_WEIGHTS["schedule"]

    # --- 信号 4: 对话轮次多 ---
    round_score = _detect_long_conversation(user_msgs_with_current)
    signals["long_conversation"] = round_score
    score += round_score * _SIGNAL_WEIGHTS["long_conversation"]

    # --- 信号 5: 用户回复变慢 ---
    if has_baseline:
        slowdown_score = _detect_slowdown_vs_baseline(user_msgs, user_baseline)
    else:
        slowdown_score = _detect_reply_slowdown(user_msgs)
    signals["reply_slowdown"] = slowdown_score
    score += slowdown_score * _SIGNAL_WEIGHTS["reply_slowdown"]

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

    # 真人化 P2-2：区分「忙」和「烦」
    fatigue_type = _classify_fatigue_type(level, signals, has_baseline)

    return {
        "level": level,
        "hint": hint,
        "score": round(score, 2),
        "signals": signals,
        "fatigue_type": fatigue_type,  # 真人化 P2-2
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


def get_closing_message(level: int, schedule=None, fatigue_type: str = "") -> Optional[str]:
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

    # 真人化 P2-2："烦"类型用更温柔收尾，"忙"类型简短识趣
    if fatigue_type == "烦":
        return random.choice([
            "好啦好啦，不打扰你啦~",
            "嗯嗯，你先静一静~",
            "那先不烦你了，回头见~",
            "好，我乖乖的不吵你了~",
        ])
    elif fatigue_type == "忙":
        return random.choice([
            "嗯嗯，你忙吧~",
            "好，不耽误你啦~",
            "行嘞，回头有空再聊~",
        ])

    # 正常时段
    return random.choice([
        "好啦，那你先忙~",
        "嗯嗯，回头再聊~",
        "行嘞，那先这样~",
        "好~有空再聊",
    ])


# ═══════════════════════════════════════════════════════════════
# 真人化 P2-2：基线学习 + 忙/烦区分
# ═══════════════════════════════════════════════════════════════


def _detect_shortening_vs_baseline(
    user_msgs: list, baseline: dict
) -> float:
    """检测消息长度是否偏离基线（真人化 P2-2）。

    与绝对阈值不同，这里判断的是相对该用户正常回复长度的偏离。
    """
    if len(user_msgs) < 6:
        return 0.0

    recent = user_msgs[-6:]
    recent_lens = [len(m.get("content", "")) for m in recent]
    avg_recent = sum(recent_lens) / len(recent_lens) if recent_lens else 0
    if avg_recent == 0:
        return 0.0

    baseline_len = baseline.get("avg_reply_length", 0)
    if baseline_len <= 0:
        return 0.0

    ratio = avg_recent / baseline_len

    if ratio < 0.3:
        return 2.5
    elif ratio < 0.5:
        return 1.5
    elif ratio < 0.7:
        return 0.5
    return 0.0


def _detect_slowdown_vs_baseline(
    user_msgs: list, baseline: dict
) -> float:
    """检测回复速度是否偏离基线（真人化 P2-2）。"""
    if len(user_msgs) < 5:
        return 0.0

    timestamps = [m.get("timestamp", 0) for m in user_msgs if m.get("timestamp")]
    if len(timestamps) < 5:
        return 0.0

    recent_ts = timestamps[-5:]
    intervals = [recent_ts[i+1] - recent_ts[i] for i in range(len(recent_ts)-1)]
    intervals = [i for i in intervals if i > 0]

    if len(intervals) < 3:
        return 0.0

    avg_recent = sum(intervals) / len(intervals)

    baseline_gap = baseline.get("avg_reply_gap", 0)
    if baseline_gap <= 0:
        return 0.0

    ratio = avg_recent / baseline_gap

    if ratio > 3.0:
        return 2.0
    elif ratio > 2.0:
        return 1.0
    elif ratio > 1.5:
        return 0.5
    return 0.0


def _classify_fatigue_type(
    level: int, signals: dict, has_baseline: bool
) -> str:
    """区分「忙」和「烦」（真人化 P2-2，审计 audit-3-2）。

    - 「忙」：间隔拉长但回复内容长度正常
    - 「烦」：间隔拉长 + 回复变短

    Returns:
        "忙" / "烦" / ""
    """
    if level < 1:
        return ""

    slowdown = signals.get("reply_slowdown", 0)
    shortening = signals.get("message_shortening", 0)

    # 间隔拉长但内容不短 → 忙
    if slowdown > 0.5 and shortening < 0.5:
        return "忙"

    # 间隔拉长 + 内容变短 → 烦
    if slowdown > 0.5 and shortening >= 0.5:
        return "烦"

    # 仅内容变短 → 轻度烦
    if shortening >= 1.0:
        return "烦"

    return ""


def compute_user_baseline_from_messages(
    user_msgs: List[Dict[str, Any]]
) -> Tuple[float, float, float, float]:
    """从用户消息计算单次样本统计（供基线更新用）。

    Returns:
        (avg_reply_length, avg_reply_gap, sticker_rate, question_rate)
    """
    if len(user_msgs) < 4:
        return 0.0, 0.0, 0.0, 0.0

    # 回复长度
    lengths = [len(m.get("content", "")) for m in user_msgs]
    avg_len = sum(lengths) / len(lengths) if lengths else 0.0

    # 回复间隔
    timestamps = [m.get("timestamp", 0) for m in user_msgs if m.get("timestamp")]
    intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    intervals = [i for i in intervals if i > 0]
    avg_gap = sum(intervals) / len(intervals) if intervals else 0.0

    # 表情包频率
    try:
        from .nonverbal_signals import _count_stickers
        sticker_counts = [_count_stickers(m.get("content", "")) for m in user_msgs]
        sticker_rate = sum(sticker_counts) / len(sticker_counts) if sticker_counts else 0.0
    except ImportError:
        sticker_rate = 0.0

    # 反问频率
    import re
    question_count = 0
    for m in user_msgs:
        content = m.get("content", "")
        if re.search(r'[?？]', content):
            question_count += 1
    question_rate = question_count / len(user_msgs) if user_msgs else 0.0

    return avg_len, avg_gap, sticker_rate, question_rate


async def update_user_baseline_async(user_id: str, user_msgs: List[Dict[str, Any]]) -> bool:
    """异步更新用户回复风格基线。"""
    avg_len, avg_gap, sticker_rate, question_rate = \
        compute_user_baseline_from_messages(user_msgs)

    if avg_len <= 0:
        return False

    try:
        from . import db_proactive
        return await db_proactive.update_fatigue_baseline(
            user_id, avg_len, avg_gap, sticker_rate, question_rate
        )
    except Exception:
        return False


async def get_user_baseline_async(user_id: str) -> dict:
    """异步获取用户基线（真人化 P2-2）。"""
    try:
        from . import db_proactive
        return await db_proactive.get_fatigue_baseline(user_id)
    except Exception:
        return {"sample_count": 0, "avg_reply_length": 0, "avg_reply_gap": 0,
                "sticker_rate": 0, "question_rate": 0, "last_updated": 0}


def has_sufficient_baseline(baseline: dict) -> bool:
    """检查基线样本数是否足够（真人化 P2-2：需 ≥20 条）。"""
    return baseline.get("sample_count", 0) >= _MIN_BASELINE_SAMPLES


def compute_correlation_adjusted_score(
    shortening_score: float, slowdown_score: float
) -> float:
    """计算考虑相关性的联合得分（真人化 P2-2，审计 audit-3-1）。

    当回复变短和回复变慢同时出现时，不简单相加，
    而是用加权公式降低重复计数的贡献。

    公式：combined = max(s, l) + min(s, l) * (1 - r)
    其中 r 为皮尔逊相关系数 (0.3)
    """
    if shortening_score <= 0 and slowdown_score <= 0:
        return 0.0

    s = shortening_score
    l = slowdown_score
    max_val = max(s, l)
    min_val = min(s, l)

    return max_val + min_val * (1.0 - _R_SHORTENING_SLOWDOWN)