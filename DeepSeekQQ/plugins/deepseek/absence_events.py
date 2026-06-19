"""缺席事件生成器 — 模拟真人"不在线"的各种场景。

真人缺席模型（P0-3）:
1. 上课/开会 → 完全不能回（1-3h）
2. 打游戏上头 → 看到但懒得切（5-30min）
3. 午睡/小憩 → 完全不能回（20-90min）
4. 做饭/吃饭 → 单手慢回（10-40min）
5. 手机没电 → 完全不能回（30-120min）
6. 通勤/走路 → 能发语音不能打字（5-30min）

由 schedule 状态 + 随机概率生成缺席事件。
缺席时写入 CausalContext → handler 延迟回复或跳过。
恢复后生成自然解释："刚在打游戏没看到"。
"""

import random
import time as _time
from dataclasses import dataclass
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger

from .causal_context import get_cc
from .causal_context import get_cc_safe

# Phase 5.2 参数调优：缺席概率可通过 config.py 覆盖
try:
    from .config import HUMANIZE_TUNING_ABSENCE_ACTIVE_PROB as _ABSENCE_ACTIVE_PROB
except ImportError:
    _ABSENCE_ACTIVE_PROB = 0.05


# ═══════════════════════════════════════════════════════════════
# 缺席类型定义
# ═══════════════════════════════════════════════════════════════


@dataclass
class AbsenceType:
    """缺席类型配置。"""

    key: str  # 标识
    reason: str  # 人类可读原因
    emoji: str  # 表情符号
    min_minutes: int  # 最短持续时间
    max_minutes: int  # 最长持续时间
    can_glance: bool  # 能否瞄一眼手机
    can_reply_short: bool  # 能否简短回复
    reply_speed_factor: float  # 回复速度系数（如果 can_reply_short）
    trigger_weight: int  # 触发权重
    recovery_templates: List[str]  # 恢复后的自然解释模板


# ═══════════════════════════════════════════════════════════════
# 缺席类型配置
# ═══════════════════════════════════════════════════════════════

ABSENCE_TYPES: Dict[str, AbsenceType] = {
    "class": AbsenceType(
        key="class",
        reason="在上课",
        emoji="📝",
        min_minutes=50,
        max_minutes=180,
        can_glance=True,
        can_reply_short=False,
        reply_speed_factor=0.0,
        trigger_weight=20,
        recovery_templates=[
            "刚下课！老师拖堂了😩",
            "下课了下课了，累死了",
            "终于下课了~你刚说什么？",
            "课上完了！刚才偷偷看了一眼消息但不敢回",
        ],
    ),
    "gaming": AbsenceType(
        key="gaming",
        reason="在打游戏",
        emoji="🎮",
        min_minutes=5,
        max_minutes=30,
        can_glance=True,
        can_reply_short=True,
        reply_speed_factor=0.3,
        trigger_weight=25,
        recovery_templates=[
            "刚打完这局！没看到消息",
            "打完了打完了，输了😭",
            "刚在打游戏没注意到~",
            "终于打完这局了，手都酸了",
            "赢了赢了！刚打完一局",
        ],
    ),
    "nap": AbsenceType(
        key="nap",
        reason="在午睡",
        emoji="😴",
        min_minutes=20,
        max_minutes=90,
        can_glance=False,
        can_reply_short=False,
        reply_speed_factor=0.0,
        trigger_weight=15,
        recovery_templates=[
            "刚睡醒…还有点迷糊",
            "睡着了睡着了😴",
            "不小心睡着了…",
            "刚醒，睡得好香",
            "午睡刚醒，还有点懵",
        ],
    ),
    "cooking": AbsenceType(
        key="cooking",
        reason="在做饭",
        emoji="🍳",
        min_minutes=10,
        max_minutes=40,
        can_glance=True,
        can_reply_short=True,
        reply_speed_factor=0.5,
        trigger_weight=10,
        recovery_templates=[
            "做完饭了！刚才手里都是油",
            "终于做好了，饿死我了",
            "刚做完饭，手忙脚乱的",
            "饭做好了~刚才没法看手机",
        ],
    ),
    "phone_dead": AbsenceType(
        key="phone_dead",
        reason="手机没电了",
        emoji="🔋",
        min_minutes=30,
        max_minutes=120,
        can_glance=False,
        can_reply_short=False,
        reply_speed_factor=0.0,
        trigger_weight=5,
        recovery_templates=[
            "手机没电了，刚充上！",
            "充电器找半天…终于充上了",
            "手机没电了…才充上",
            "啊手机刚才没电了，充电宝也没带",
        ],
    ),
    "commuting": AbsenceType(
        key="commuting",
        reason="在路上",
        emoji="🚶",
        min_minutes=5,
        max_minutes=30,
        can_glance=True,
        can_reply_short=True,
        reply_speed_factor=0.4,
        trigger_weight=10,
        recovery_templates=[
            "到地方了！刚才在路上",
            "刚在地铁上信号不好~",
            "到了到了，走了一路",
            "刚在路上不方便打字",
        ],
    ),
}


# ═══════════════════════════════════════════════════════════════
# 状态跟踪
# ═══════════════════════════════════════════════════════════════

# 连续缺席计数（防止同一会话重复解释）
_consecutive_absences: Dict[str, int] = {}

# 上次恢复解释时间（防止短时间内多次解释）
_last_recovery_explanation: Dict[str, float] = {}


def _get_absence_probability(schedule_period: str, cc) -> float:
    """根据当前作息时段计算缺席概率。

    不同时段的缺席概率不同：
    - 上课/工作时间：较高概率缺席
    - 晚间自由时间：低概率缺席
    - 深夜：中等概率（可能在刷手机，也可能睡着了）
    """
    base_probs = {
        "sleeping": 0.80,   # 睡觉时大概率缺席
        "waking": 0.15,     # 刚醒时较低概率
        "active": _ABSENCE_ACTIVE_PROB,  # 活跃时低概率（可配置）
        "meal": 0.30,       # 吃饭时中等概率
        "lazy": 0.20,       # 摸鱼时低中等概率
        "night_owl": 0.15,  # 深夜中等概率
        "skip_class": 0.10, # 逃课时低概率
    }
    return base_probs.get(schedule_period, _ABSENCE_ACTIVE_PROB)


def _select_absence_type(schedule_period: str, cc) -> Optional[AbsenceType]:
    """根据当前作息时段选择合适的缺席类型。

    Returns:
        AbsenceType 或 None（本次不触发缺席）
    """
    hour = cc.virtual_hour
    weekday = cc.virtual_weekday
    is_weekend = weekday >= 5

    # 根据时段构建候选列表
    candidates: List[Tuple[AbsenceType, int]] = []

    for at in ABSENCE_TYPES.values():
        weight = at.trigger_weight

        # 时段修正
        if at.key == "class":
            if is_weekend or hour < 8 or hour > 18:
                continue  # 非上课时间不触发上课缺席
            weight *= 1.5
        elif at.key == "nap":
            if hour < 12 or hour > 16:
                continue  # 非午睡时间不触发
            if schedule_period in ("sleeping", "lazy"):
                weight *= 2.0
        elif at.key == "gaming":
            if hour < 17 or hour > 2:
                weight *= 0.3  # 非晚间降低权重
            if schedule_period in ("active", "night_owl"):
                weight *= 1.5
        elif at.key == "cooking":
            if hour < 11 or hour > 20:
                continue  # 非饭点不触发
        elif at.key == "commuting":
            if hour < 7 or hour > 20:
                weight *= 0.5
        elif at.key == "phone_dead":
            # 手机没电随时可能，但晚间概率更高
            if 18 <= hour <= 23:
                weight *= 1.3

        candidates.append((at, weight))

    if not candidates:
        return None

    # 按权重随机选择
    total_weight = sum(w for _, w in candidates)
    if total_weight <= 0:
        return None

    r = random.random() * total_weight
    cumulative = 0
    for at, w in candidates:
        cumulative += w
        if r <= cumulative:
            return at

    return candidates[-1][0] if candidates else None


def maybe_generate_absence(session_id: str) -> Optional[dict]:
    """检查是否应该触发缺席事件。

    在每次收到消息时调用。如果 bot 当前处于可能缺席的状态，
    根据概率决定是否进入缺席。

    Args:
        session_id: 会话 ID

    Returns:
        None 表示不触发缺席
        dict 包含 absence 信息，供 handler 使用
    """
    cc = get_cc_safe(session_id)
    if not cc:
        return None

    # 如果已经在缺席中，检查是否应该恢复
    if cc.is_absent:
        if _time.time() >= cc.absence_until:
            reason = cc.clear_absent()
            if reason:
                logger.info(f"[缺席] 恢复: session={session_id[:8]} reason={reason}")
                return {
                    "type": "recovery",
                    "reason": reason,
                }
        return None

    # 检查触发概率
    prob = _get_absence_probability(cc.schedule_period, cc)
    if random.random() > prob:
        return None

    # 选择缺席类型
    absence_type = _select_absence_type(cc.schedule_period, cc)
    if not absence_type:
        return None

    # 计算持续时间
    duration_minutes = random.randint(absence_type.min_minutes, absence_type.max_minutes)
    duration_seconds = duration_minutes * 60
    until = _time.time() + duration_seconds

    # 写入 CausalContext
    cc.set_absent(absence_type.reason, until)

    # 连续缺席计数
    key = f"{session_id}:{absence_type.key}"
    _consecutive_absences[key] = _consecutive_absences.get(key, 0) + 1

    logger.info(
        f"[缺席] 触发: session={session_id[:8]} "
        f"reason={absence_type.reason} duration={duration_minutes}min"
    )

    return {
        "type": "absence",
        "reason": absence_type.reason,
        "emoji": absence_type.emoji,
        "can_glance": absence_type.can_glance,
        "can_reply_short": absence_type.can_reply_short,
        "reply_speed_factor": absence_type.reply_speed_factor,
        "until": until,
        "duration_minutes": duration_minutes,
    }


def get_absence_recovery_message(session_id: str) -> Optional[str]:
    """获取缺席恢复后的自然解释消息。

    只有刚从缺席中恢复时才返回消息（防止重复解释）。

    Args:
        session_id: 会话 ID

    Returns:
        恢复解释消息或 None
    """
    cc = get_cc_safe(session_id)
    if not cc:
        return None

    # 检查因果链中最近是否有恢复事件
    recent = cc.get_recent_events(3)
    recovery_events = [
        e for e in recent
        if e.source == "absence_events" and "缺席结束" in e.cause
    ]
    if not recovery_events:
        return None

    # 防重复：10分钟内不重复解释
    last_time = _last_recovery_explanation.get(session_id, 0)
    if _time.time() - last_time < 600:
        return None

    # 获取恢复原因对应的缺席类型
    cause = recovery_events[-1].cause
    reason = cause.replace("缺席结束: ", "")

    # 查找匹配的缺席类型
    for at in ABSENCE_TYPES.values():
        if at.reason == reason and at.recovery_templates:
            msg = random.choice(at.recovery_templates)
            _last_recovery_explanation[session_id] = _time.time()
            return msg

    return None


def should_skip_reply(session_id: str) -> Tuple[bool, str]:
    """判断是否应该跳过/延迟当前回复。

    在 handler pipeline 中调用，根据缺席状态决定是否回复。

    Returns:
        (should_skip, reason)
        - should_skip=True: 完全跳过回复
        - should_skip=False, reason=""   : 正常回复
        - should_skip=False, reason="delayed": 延迟回复
    """
    cc = get_cc_safe(session_id)
    if not cc or not cc.is_absent:
        return (False, "")

    # 检查是否该恢复了
    if _time.time() >= cc.absence_until:
        cc.clear_absent()
        return (False, "")

    # 获取缺席类型
    for at in ABSENCE_TYPES.values():
        if at.reason == cc.absence_reason:
            if not at.can_glance:
                return (True, at.reason)
            elif at.can_reply_short:
                return (False, "delayed")
            break

    # 默认：不能回复
    return (True, cc.absence_reason)


def get_absence_reply_speed(session_id: str) -> float:
    """获取当前缺席状态下的回复速度系数。

    Returns:
        1.0 表示正常速度，<1.0 表示减速
    """
    cc = get_cc_safe(session_id)
    if not cc or not cc.is_absent:
        return 1.0

    for at in ABSENCE_TYPES.values():
        if at.reason == cc.absence_reason:
            return at.reply_speed_factor

    return 0.5


def reset_absence_state() -> None:
    """重置所有缺席相关状态（测试清理用）。"""
    _consecutive_absences.clear()
    _last_recovery_explanation.clear()
