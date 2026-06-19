"""非语言信号检测器 — 从用户行为模式中提取隐藏情绪信号。

真人能感知的非语言信号（文字聊天中）：
1. 回复间隔趋势 → 拉长=在忙/不感兴趣，缩短=兴趣增加
2. 回复长度趋势 → 越来越短=疲劳/敷衍，越来越长=投入
3. 表情包频率变化 → 突然不用=情绪波动，突然增多=兴奋/掩饰
4. 反问频率变化 → 不再反问=不想聊/疲劳
5. 语气词突变 → "哈哈哈"/"www" 突然消失=情绪低落
6. 撤回消息检测 → 可能说了什么又犹豫

关键设计（audit-2-2）：非语言信号不仅用于疲劳判定，还要反馈给情绪系统。
- 回复变短+间隔拉长 → 反馈"被冷落"情绪给 bot
- 突然不用表情包 → 反馈"不安"情绪给 bot
- 正常波动不误报（±20% 内不算趋势变更）

Phase 2.4: 真人化改造 P1-5
"""

import re as _re
import time as _time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class NonVerbalSignals:
    """非语言信号集合 — 从最近消息中提取的行为模式变化。"""

    # ── 回复间隔 ──
    avg_reply_gap: float = 0.0               # 平均回复间隔（秒）
    gap_trend: str = "stable"                # stable / lengthening / shortening
    gap_anomaly: bool = False                # 是否出现异常长间隔（>3σ）

    # ── 回复长度 ──
    avg_reply_length: int = 0                # 平均回复字数
    length_trend: str = "stable"             # stable / shortening / lengthening

    # ── 表情包 ──
    sticker_frequency: float = 0.0           # 表情包/消息 比率
    sticker_change: str = "normal"           # normal / stopped / increased

    # ── 反问 ──
    question_frequency: float = 0.0          # 反问句比率
    question_change: str = "normal"          # normal / declined / increased

    # ── 语气词 ──
    tone_shift_detected: bool = False        # 语气词突然消失/出现
    tone_shift_detail: str = ""              # 描述语气变化

    # ── 撤回 ──
    recall_detected: bool = False            # 最近有撤回消息

    # ── 整体 ──
    signal_count: int = 0                    # 异常信号总数
    cold_shoulder_score: float = 0.0         # "被冷落"得分（0-1）

    def has_any_signal(self) -> bool:
        """是否有任何异常信号。"""
        return (
            self.gap_trend != "stable"
            or self.length_trend != "stable"
            or self.sticker_change != "normal"
            or self.question_change != "normal"
            or self.tone_shift_detected
            or self.recall_detected
        )

    def get_emotion_feedback(self) -> Optional[Dict[str, Any]]:
        """将非语言信号转化为情绪反馈（audit-2-2）。

        Returns:
            None 或 {"emotion": str, "intensity": float, "reason": str}
        """
        if self.cold_shoulder_score >= 0.6:
            return {
                "emotion": "被冷落",
                "intensity": min(0.6, self.cold_shoulder_score),
                "reason": f"对方回复变短且间隔拉长（冷落得分={self.cold_shoulder_score:.2f}）",
            }

        if self.sticker_change == "stopped" and self.length_trend == "shortening":
            return {
                "emotion": "不安",
                "intensity": 0.3,
                "reason": "对方突然不用表情包且回复变短，可能情绪不好",
            }

        if self.tone_shift_detected and self.gap_trend == "lengthening":
            return {
                "emotion": "担心",
                "intensity": 0.25,
                "reason": f"对方语气突变且回复变慢: {self.tone_shift_detail}",
            }

        if self.gap_anomaly and not self.recall_detected:
            return {
                "emotion": "被冷落",
                "intensity": 0.2,
                "reason": "对方突然长时间不回复",
            }

        return None


# ═══════════════════════════════════════════════════════════════
# 回复间隔分析
# ═══════════════════════════════════════════════════════════════

def _analyze_reply_gaps(user_msgs: List[Dict]) -> Tuple[float, str, bool]:
    """分析回复间隔趋势。

    Returns:
        (avg_gap, trend, anomaly)
    """
    if len(user_msgs) < 5:
        return 0.0, "stable", False

    timestamps = [m.get("timestamp", 0) for m in user_msgs if m.get("timestamp")]
    if len(timestamps) < 5:
        return 0.0, "stable", False

    # 计算间隔
    intervals = [timestamps[i+1] - timestamps[i] for i in range(len(timestamps)-1)]
    intervals = [i for i in intervals if i > 0]
    if len(intervals) < 3:
        return 0.0, "stable", False

    avg_gap = sum(intervals) / len(intervals)

    # 前后半对比
    mid = len(intervals) // 2
    first_half = intervals[:mid]
    second_half = intervals[mid:]

    avg_first = sum(first_half) / len(first_half) if first_half else 1
    avg_second = sum(second_half) / len(second_half) if second_half else 1

    if avg_first == 0:
        return avg_gap, "stable", False

    ratio = avg_second / avg_first

    # 趋势判定（阈值：20%变化才算趋势）
    if ratio > 1.4:
        trend = "lengthening"
    elif ratio < 0.7:
        trend = "shortening"
    else:
        trend = "stable"

    # 异常检测：最近间隔 > 前N条平均的 5 倍 且 > 300s（5分钟）
    if len(intervals) >= 4:
        # 取前 N-1 条间隔的平均
        prev_intervals = intervals[:-1]
        prev_avg = sum(prev_intervals) / len(prev_intervals) if prev_intervals else 1
        last_interval = intervals[-1]
        # 最后一条间隔 > 5x 之前平均 且 > 5分钟
        anomaly = last_interval > max(prev_avg * 5, 300)
    else:
        anomaly = False

    return avg_gap, trend, anomaly


# ═══════════════════════════════════════════════════════════════
# 回复长度分析
# ═══════════════════════════════════════════════════════════════

def _analyze_reply_length(user_msgs: List[Dict]) -> Tuple[int, str]:
    """分析回复长度趋势。

    Returns:
        (avg_length, trend)
    """
    if len(user_msgs) < 6:
        return 0, "stable"

    recent = user_msgs[-6:]
    recent_lens = [len(m.get("content", "")) for m in recent]

    avg_len = sum(recent_lens) // len(recent_lens)

    first_half = recent_lens[:3]
    second_half = recent_lens[3:]

    avg_first = sum(first_half) / 3 if first_half else 0
    avg_second = sum(second_half) / 3 if second_half else 0

    if avg_first <= 1:
        return avg_len, "stable"

    ratio = avg_second / avg_first

    if ratio < 0.5:
        return avg_len, "shortening"
    elif ratio > 1.8:
        return avg_len, "lengthening"

    return avg_len, "stable"


# ═══════════════════════════════════════════════════════════════
# 表情包频率分析
# ═══════════════════════════════════════════════════════════════

# 常见表情包模式
_STICKER_PATTERNS = [
    _re.compile(r'\[CQ:image,'),
    _re.compile(r'\[CQ:face,'),
    _re.compile(r'\[表情\]'),
    _re.compile(r'\[贴图\]'),
    _re.compile(r'\[sticker\]', _re.IGNORECASE),
]

# 颜文字/emoji 也算表情
_EMOJI_STICKER = _re.compile(r'[\U0001F300-\U0001F9FF☀-➿‍️]|[©®™ℹ]|[\(（][一-鿿\w]+[\)）]')


def _count_stickers(content: str) -> int:
    """统计消息中的表情包/表情数量。"""
    count = 0
    for pat in _STICKER_PATTERNS:
        count += len(pat.findall(content))
    # 颜文字检测（简化）：括号内短内容
    yanwen = _re.findall(r'[\(（][\w一-鿿·]{1,6}[\)）]', content)
    count += len(yanwen)
    # Emoji
    count += len(_EMOJI_STICKER.findall(content))
    return count


def _analyze_stickers(user_msgs: List[Dict]) -> Tuple[float, str]:
    """分析表情包使用频率变化。

    Returns:
        (frequency, change)
    """
    if len(user_msgs) < 8:
        return 0.0, "normal"

    recent = user_msgs[-8:]
    first_half = recent[:4]
    second_half = recent[4:]

    first_stickers = sum(_count_stickers(m.get("content", "")) for m in first_half)
    second_stickers = sum(_count_stickers(m.get("content", "")) for m in second_half)

    first_total = max(1, len(first_half))
    second_total = max(1, len(second_half))

    freq = (first_stickers + second_stickers) / (first_total + second_total)

    first_rate = first_stickers / first_total
    second_rate = second_stickers / second_total

    if first_rate > 0.3 and second_rate == 0:
        return freq, "stopped"       # 从有到无
    elif first_rate == 0 and second_rate > 0.3:
        return freq, "increased"     # 从无到有
    elif first_rate > 0 and second_rate / max(0.01, first_rate) > 2.5:
        return freq, "increased"     # 大幅增加
    elif second_rate > 0 and first_rate / max(0.01, second_rate) > 2.5:
        return freq, "stopped"       # 大幅减少

    return freq, "normal"


# ═══════════════════════════════════════════════════════════════
# 反问频率分析
# ═══════════════════════════════════════════════════════════════

# 反问句模式
_QUESTION_PATTERNS = [
    _re.compile(r'[你妳][觉得认为觉]?[得会]?[怎样如何怎么]'),
    _re.compile(r'[呢吗吧啊呀]？*$'),
    _re.compile(r'[?？]'),
    _re.compile(r'什么|为啥|为什么|干嘛|怎么|哪[个些里]'),
    _re.compile(r'有没有|是不是|行不行|好不好|可不可以|能不能'),
]


def _count_questions(content: str) -> int:
    """估算消息中的反问数量。"""
    count = 0
    for pat in _QUESTION_PATTERNS:
        count += len(pat.findall(content))
    # 一个消息最多算2个问题
    return min(count, 2)


def _analyze_questions(user_msgs: List[Dict]) -> Tuple[float, str]:
    """分析反问频率变化。

    Returns:
        (frequency, change)
    """
    if len(user_msgs) < 8:
        return 0.0, "normal"

    recent = user_msgs[-8:]
    first_half = recent[:4]
    second_half = recent[4:]

    first_q = sum(_count_questions(m.get("content", "")) for m in first_half)
    second_q = sum(_count_questions(m.get("content", "")) for m in second_half)

    total = len(first_half) + len(second_half)
    freq = (first_q + second_q) / max(1, total)

    first_rate = first_q / max(1, len(first_half))
    second_rate = second_q / max(1, len(second_half))

    if first_rate > 0.3 and second_rate == 0:
        return freq, "declined"
    elif first_rate > 0 and second_rate / max(0.01, first_rate) < 0.3:
        return freq, "declined"
    elif second_rate / max(0.01, first_rate) > 2.5:
        return freq, "increased"

    return freq, "normal"


# ═══════════════════════════════════════════════════════════════
# 语气词突变检测
# ═══════════════════════════════════════════════════════════════

# 语气词/笑声词
_TONE_MARKERS = [
    _re.compile(r'哈{2,}'),      # 哈哈哈
    _re.compile(r'[wｗ]{2,}'),    # www
    _re.compile(r'草{2,}'),      # 草草草
    _re.compile(r'笑死'),        # 笑死
    _re.compile(r'乐'),          # 乐
    _re.compile(r'[！!]{2,}'),   # 多个感叹号
    _re.compile(r'~{2,}'),       # 多个波浪号
    _re.compile(r'[.。]{3,}'),   # 省略号
]


def _detect_tone_shift(user_msgs: List[Dict]) -> Tuple[bool, str]:
    """检测语气词突然变化。

    Returns:
        (shift_detected, detail)
    """
    if len(user_msgs) < 8:
        return False, ""

    recent = user_msgs[-8:]
    first_half = recent[:4]
    second_half = recent[4:]

    def _count_tone(content: str) -> int:
        count = 0
        for pat in _TONE_MARKERS:
            count += len(pat.findall(content))
        return count

    first_tone = sum(_count_tone(m.get("content", "")) for m in first_half)
    second_tone = sum(_count_tone(m.get("content", "")) for m in second_half)

    first_rate = first_tone / max(1, len(first_half))
    second_rate = second_tone / max(1, len(second_half))

    if first_rate > 0.5 and second_rate == 0:
        return True, "语气词突然消失（之前常用'哈哈哈'/'www'等）"
    elif first_rate == 0 and second_rate > 0.5:
        return True, "突然开始大量使用语气词"

    return False, ""


# ═══════════════════════════════════════════════════════════════
# 撤回消息检测
# ═══════════════════════════════════════════════════════════════

# 撤回检测需要外部传入（recall 事件在 handler 中处理）
# 这里提供接口供 handler 调用


def _check_recall(recent_recalls: int = 0) -> bool:
    """检查最近是否有撤回消息。

    Args:
        recent_recalls: 最近5分钟内的撤回次数（由 handler 传入）
    """
    return recent_recalls > 0


# ═══════════════════════════════════════════════════════════════
# 主分析函数
# ═══════════════════════════════════════════════════════════════

# 全局存储：每个 session 的最近撤回次数
_session_recalls: Dict[str, List[float]] = {}  # session_id → [timestamps]


def record_recall(session_id: str) -> None:
    """记录一次撤回事件。"""
    now = _time.time()
    if session_id not in _session_recalls:
        _session_recalls[session_id] = []
    _session_recalls[session_id].append(now)
    # 清理5分钟前的记录
    cutoff = now - 300
    _session_recalls[session_id] = [t for t in _session_recalls[session_id] if t > cutoff]


def get_recent_recalls(session_id: str) -> int:
    """获取最近5分钟内的撤回次数。"""
    now = _time.time()
    cutoff = now - 300
    recalls = _session_recalls.get(session_id, [])
    return sum(1 for t in recalls if t > cutoff)


def analyze_nonverbal(
    session_id: str,
    recent_memories: List[Dict[str, Any]],
    current_msg: str = "",
) -> NonVerbalSignals:
    """分析非语言信号 — 主入口。

    Args:
        session_id: 会话ID
        recent_memories: 最近消息列表 [{role, content, timestamp}, ...]
        current_msg: 当前用户消息

    Returns:
        NonVerbalSignals 包含所有检测到的信号
    """
    signals = NonVerbalSignals()

    # 提取用户消息（按时间排序）
    user_msgs = [m for m in recent_memories if m.get("role") == "user"]
    if not user_msgs:
        return signals

    # 1. 回复间隔分析
    signals.avg_reply_gap, signals.gap_trend, signals.gap_anomaly = \
        _analyze_reply_gaps(user_msgs)

    # 2. 回复长度分析
    signals.avg_reply_length, signals.length_trend = \
        _analyze_reply_length(user_msgs)

    # 3. 表情包频率分析
    signals.sticker_frequency, signals.sticker_change = \
        _analyze_stickers(user_msgs)

    # 4. 反问频率分析
    signals.question_frequency, signals.question_change = \
        _analyze_questions(user_msgs)

    # 5. 语气词突变
    signals.tone_shift_detected, signals.tone_shift_detail = \
        _detect_tone_shift(user_msgs)

    # 6. 撤回检测
    signals.recall_detected = get_recent_recalls(session_id) > 0

    # 统计异常信号数
    signals.signal_count = sum([
        1 if signals.gap_trend != "stable" else 0,
        1 if signals.length_trend != "stable" else 0,
        1 if signals.sticker_change != "normal" else 0,
        1 if signals.question_change != "normal" else 0,
        1 if signals.tone_shift_detected else 0,
        1 if signals.recall_detected else 0,
        1 if signals.gap_anomaly else 0,
    ])

    # 计算"被冷落"得分（audit-2-2 核心）
    signals.cold_shoulder_score = _compute_cold_shoulder(signals)

    if signals.has_any_signal():
        logger.debug(
            f"[非语言信号] session={session_id[:8]} "
            f"gap={signals.gap_trend} len={signals.length_trend} "
            f"sticker={signals.sticker_change} question={signals.question_change} "
            f"tone_shift={signals.tone_shift_detected} recall={signals.recall_detected} "
            f"cold_shoulder={signals.cold_shoulder_score:.2f}"
        )

    # 写入 CausalContext
    _sync_to_causal_context(session_id, signals)

    return signals


def _compute_cold_shoulder(signals: NonVerbalSignals) -> float:
    """计算"被冷落"得分，综合多个信号。

    得分 0-1：越高越像被冷落。
    """
    score = 0.0

    # 间隔拉长 + 长度缩短 = 强烈被冷落信号
    if signals.gap_trend == "lengthening" and signals.length_trend == "shortening":
        score += 0.5
    elif signals.gap_trend == "lengthening":
        score += 0.2
    elif signals.length_trend == "shortening":
        score += 0.2

    # 表情包停了
    if signals.sticker_change == "stopped":
        score += 0.15

    # 不再反问
    if signals.question_change == "declined":
        score += 0.1

    # 异常长间隔
    if signals.gap_anomaly:
        score += 0.15

    # 语气词消失
    if signals.tone_shift_detected:
        score += 0.1

    # 撤回消息
    if signals.recall_detected:
        score += 0.05

    return min(1.0, score)


def _sync_to_causal_context(session_id: str, signals: NonVerbalSignals) -> None:
    """将非语言信号同步到 CausalContext（audit-2-2 关键链路）。"""
    if not signals.has_any_signal():
        return

    try:
        from .causal_context import get_cc
        cc = get_cc(session_id)

        # 反馈给情绪系统
        feedback = signals.get_emotion_feedback()
        if feedback:
            # 低强度情绪反馈（不会覆盖已有强烈情绪）
            if cc.emotion_intensity < 0.4:
                cc.update_emotion(
                    emotion=feedback["emotion"],
                    intensity=feedback["intensity"],
                    source="nonverbal_signals",
                )
                logger.info(
                    f"[非语言→情绪] {feedback['emotion']}({feedback['intensity']:.2f}) "
                    f"reason: {feedback['reason']}"
                )

        # 记录因果事件
        if signals.cold_shoulder_score > 0.3:
            cc._add_event(
                source="nonverbal_signals",
                cause=f"检测到非语言信号: gap={signals.gap_trend}, len={signals.length_trend}, sticker={signals.sticker_change}",
                effect=f"冷落得分={signals.cold_shoulder_score:.2f}" + (
                    f" → 情绪反馈={feedback['emotion']}" if feedback else ""
                ),
            )
    except Exception:
        pass  # CausalContext 不可用时静默降级


# ═══════════════════════════════════════════════════════════════
# 便捷函数：供 handler / stage_context 调用
# ═══════════════════════════════════════════════════════════════

def get_nonverbal_hint(signals: NonVerbalSignals) -> str:
    """生成非语言信号的 prompt 提示文本。"""
    if not signals.has_any_signal():
        return ""

    parts = []

    if signals.gap_trend == "lengthening":
        parts.append("对方回复间隔越来越长，可能在忙或不太想聊")
    elif signals.gap_trend == "shortening":
        parts.append("对方回复越来越快，对话正在升温")

    if signals.length_trend == "shortening":
        parts.append("对方回复越来越短，注意不要喋喋不休")
    elif signals.length_trend == "lengthening":
        parts.append("对方回复越来越长，说明对话题感兴趣")

    if signals.sticker_change == "stopped":
        parts.append("对方突然不用表情包了，可能有情绪波动")
    elif signals.sticker_change == "increased":
        parts.append("对方表情包变多了，可能在掩饰什么或心情很好")

    if signals.question_change == "declined":
        parts.append("对方不再反问，可能不想继续聊")

    if signals.tone_shift_detected:
        parts.append(signals.tone_shift_detail)

    if signals.recall_detected:
        parts.append("对方最近撤回了消息")

    if signals.cold_shoulder_score > 0.3:
        parts.append(f"综合判断：对方可能有点冷淡（得分{signals.cold_shoulder_score:.1f}），回复可以简短一些")

    if not parts:
        return ""

    return "【非语言信号】" + "；".join(parts)
