"""因果上下文总线 — 会话级共享状态对象，所有模块通过它感知彼此状态。

CausalContext 是真人化改造 P0-1 + P0-2 的核心基础设施：
- 统一时间源（virtual_now），消除各模块各自调用 datetime.now()
- 活动/情绪/疲劳/身体状态集中管理，模块间通过 ctx 感知而非独立决策
- 因果事件链记录，便于追踪"为什么 bot 这样回复"

使用方式：
    from .causal_context import get_cc, CausalEvent

    cc = get_cc(session_id)
    cc.update_activity("打游戏", intensity=0.8, can_interrupt=False)
    # ... 其他模块读取 cc.current_activity, cc.activity_intensity 等
"""

import time as _time
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timedelta
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════


@dataclass
class CausalEvent:
    """因果链中的单个事件，记录"谁导致了什么"。"""

    timestamp: float = 0.0  # time.time()
    source: str = ""  # 来源模块 (activity_sim, emotion_deep, schedule, ...)
    cause: str = ""  # 原因描述
    effect: str = ""  # 效果描述
    data: Dict[str, Any] = field(default_factory=dict)  # 附加数据

    def __repr__(self) -> str:
        return f"[{self.source}] {self.cause} → {self.effect}"


@dataclass
class CausalContext:
    """会话级因果上下文。

    每个会话（session_id）一个实例，跨消息持久化。
    所有真人化模块通过此对象共享状态，消除模块间"独立决策"。
    """

    session_id: str = ""

    # ── 活动状态（来自 activity_sim）──
    current_activity: str = ""
    activity_intensity: float = 0.5  # 0.0~1.0 活动投入程度
    activity_can_interrupt: bool = True

    # ── 身体状态（来自 schedule）──
    energy: float = 1.0  # 精力 0.0~1.0
    hunger: float = 0.0  # 饥饿 0.0~1.0
    tiredness: float = 0.0  # 疲劳 0.0~1.0
    virtual_time: datetime = field(default_factory=datetime.now)  # 统一时间源
    schedule_period: str = "active"  # sleeping/waking/active/meal/lazy/night_owl

    # ── 情绪状态（来自 emotion_deep）──
    current_emotion: str = "平静"
    emotion_intensity: float = 0.0
    emotion_valence: float = 0.0  # -1.0~1.0 效价
    emotion_arousal: float = 0.0  # 0.0~1.0 唤醒度
    emotion_hidden: bool = False  # 情绪是否被隐藏（仅微表达泄露）

    # ── 对话状态（来自 conversation_fatigue）──
    conversation_depth: int = 0  # 当前对话轮次
    is_ending: bool = False  # 对话是否在收尾
    fatigue_level: int = 0  # 疲劳等级 0-3

    # ── 缺席状态（来自 absence_events）──
    is_absent: bool = False
    absence_reason: str = ""
    absence_until: float = 0.0  # time.time() 时间戳

    # ── 因果事件记录 ──
    causal_chain: List[CausalEvent] = field(default_factory=list)
    _max_chain_length: int = 100

    # ── 承诺兑现状态 ──
    pending_promise_count: int = 0

    def update_activity(
        self,
        activity: str,
        intensity: float = 0.5,
        can_interrupt: bool = True,
    ) -> None:
        """活动切换时调用。记录因果事件。"""
        old_activity = self.current_activity
        self.current_activity = activity
        self.activity_intensity = intensity
        self.activity_can_interrupt = can_interrupt

        if old_activity and old_activity != activity:
            self._add_event(
                source="activity_sim",
                cause=f"活动从「{old_activity}」切换到「{activity}」",
                effect=f"活动强度={intensity}, 可中断={can_interrupt}",
                data={"old": old_activity, "new": activity},
            )

    def update_emotion(
        self,
        emotion: str,
        intensity: float = 0.0,
        valence: float = 0.0,
        arousal: float = 0.0,
        hidden: bool = False,
        source: str = "emotion_deep",
    ) -> None:
        """情绪变化时调用。记录因果事件。"""
        old_emotion = self.current_emotion
        self.current_emotion = emotion
        self.emotion_intensity = intensity
        self.emotion_valence = valence
        self.emotion_arousal = arousal
        self.emotion_hidden = hidden

        if old_emotion != emotion:
            self._add_event(
                source=source,
                cause=f"情绪从「{old_emotion}」变为「{emotion}」",
                effect=f"强度={intensity}, 隐藏={hidden}",
                data={"old": old_emotion, "new": emotion, "intensity": intensity},
            )

    def update_body_state(
        self,
        energy: Optional[float] = None,
        hunger: Optional[float] = None,
        tiredness: Optional[float] = None,
        schedule_period: Optional[str] = None,
    ) -> None:
        """身体状态更新（来自 schedule）。"""
        if energy is not None:
            self.energy = energy
        if hunger is not None:
            self.hunger = hunger
        if tiredness is not None:
            self.tiredness = tiredness
        if schedule_period is not None:
            old_period = self.schedule_period
            self.schedule_period = schedule_period
            if old_period != schedule_period:
                self._add_event(
                    source="schedule",
                    cause=f"作息切换: {old_period} → {schedule_period}",
                    effect=f"精力={self.energy}, 疲劳={self.tiredness}",
                )

    def update_fatigue(
        self,
        level: int = 0,
        is_ending: bool = False,
    ) -> None:
        """对话疲劳状态更新（来自 conversation_fatigue）。"""
        old_level = self.fatigue_level
        self.fatigue_level = level
        self.is_ending = is_ending

        if level >= 2 and old_level < 2:
            self._add_event(
                source="conversation_fatigue",
                cause=f"对话疲劳升至 Lv.{level}",
                effect="回复应简短，不再开启新话题" + ("，对话应自然收尾" if is_ending else ""),
            )

    def set_absent(
        self,
        reason: str,
        until: float,
    ) -> None:
        """标记为缺席状态。"""
        if not self.is_absent:
            self.is_absent = True
            self.absence_reason = reason
            self.absence_until = until
            self._add_event(
                source="absence_events",
                cause=f"进入缺席: {reason}",
                effect=f"预计恢复时间: {datetime.fromtimestamp(until).strftime('%H:%M')}",
                data={"reason": reason, "until": until},
            )

    def clear_absent(self) -> Optional[str]:
        """清除缺席状态，返回自然恢复解释。"""
        if self.is_absent:
            reason = self.absence_reason
            self.is_absent = False
            self.absence_reason = ""
            self.absence_until = 0.0
            self._add_event(
                source="absence_events",
                cause=f"缺席结束: {reason}",
                effect="恢复正常回复",
            )
            return reason
        return None

    def update_virtual_time(self, dt: Optional[datetime] = None) -> None:
        """更新虚拟时间。如果未提供，使用当前真实时间。"""
        self.virtual_time = dt or datetime.now()

    @property
    def virtual_hour(self) -> int:
        return self.virtual_time.hour

    @property
    def virtual_weekday(self) -> int:
        return self.virtual_time.weekday()

    @property
    def virtual_is_weekend(self) -> bool:
        return self.virtual_time.weekday() >= 5

    def _add_event(
        self,
        source: str,
        cause: str,
        effect: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """记录因果事件到链中。"""
        event = CausalEvent(
            timestamp=_time.time(),
            source=source,
            cause=cause,
            effect=effect,
            data=data or {},
        )
        self.causal_chain.append(event)
        # 保持链长度上限
        if len(self.causal_chain) > self._max_chain_length:
            self.causal_chain = self.causal_chain[-self._max_chain_length :]

        logger.debug(f"[因果链] {event}")

    def get_recent_events(self, n: int = 10) -> List[CausalEvent]:
        """获取最近 n 条因果事件。"""
        return self.causal_chain[-n:] if self.causal_chain else []

    def get_events_by_source(self, source: str) -> List[CausalEvent]:
        """按来源模块筛选因果事件。"""
        return [e for e in self.causal_chain if e.source == source]

    def get_summary(self) -> str:
        """生成当前状态的简短摘要（用于调试/日志）。"""
        parts = [
            f"活动={self.current_activity or '无'}",
            f"情绪={self.current_emotion}({self.emotion_intensity:.1f})",
            f"精力={self.energy:.1f}",
            f"疲劳Lv.{self.fatigue_level}",
        ]
        if self.is_absent:
            parts.append(f"缺席:{self.absence_reason}")
        return " | ".join(parts)

    def reset(self) -> None:
        """重置所有状态到默认值（测试用）。"""
        self.current_activity = ""
        self.activity_intensity = 0.5
        self.activity_can_interrupt = True
        self.energy = 1.0
        self.hunger = 0.0
        self.tiredness = 0.0
        self.virtual_time = datetime.now()
        self.schedule_period = "active"
        self.current_emotion = "平静"
        self.emotion_intensity = 0.0
        self.emotion_valence = 0.0
        self.emotion_arousal = 0.0
        self.emotion_hidden = False
        self.conversation_depth = 0
        self.is_ending = False
        self.fatigue_level = 0
        self.is_absent = False
        self.absence_reason = ""
        self.absence_until = 0.0
        self.pending_promise_count = 0
        self.causal_chain.clear()


# ═══════════════════════════════════════════════════════════════
# 会话级 CausalContext 管理
# ═══════════════════════════════════════════════════════════════

# 全局注册表：session_id → CausalContext
_cc_registry: Dict[str, CausalContext] = {}

# 最大缓存会话数（防止内存泄漏）
_MAX_CACHED_SESSIONS = 500

# 默认虚拟时间提供者（可被测试 mock）
_virtual_time_provider: Optional[callable] = None


def set_virtual_time_provider(provider: callable) -> None:
    """设置虚拟时间提供者（用于测试 mock）。

    Args:
        provider: 一个无参可调用对象，返回 datetime
    """
    global _virtual_time_provider
    _virtual_time_provider = provider


def _get_virtual_now() -> datetime:
    """获取当前虚拟时间。如果设置了 provider 则使用，否则用真实时间。"""
    if _virtual_time_provider is not None:
        return _virtual_time_provider()
    return datetime.now()


def get_cc(session_id: str) -> CausalContext:
    """获取或创建会话的 CausalContext。

    每个会话（session_id）拥有独立的 CausalContext，
    跨消息持久化直到会话结束或被清理。
    """
    if session_id not in _cc_registry:
        # 防止内存泄漏：超过上限时清理最旧的会话
        if len(_cc_registry) >= _MAX_CACHED_SESSIONS:
            oldest_key = next(iter(_cc_registry))
            del _cc_registry[oldest_key]
            logger.debug(f"[CausalContext] 清理旧会话: {oldest_key}")

        _cc_registry[session_id] = CausalContext(
            session_id=session_id,
            virtual_time=_get_virtual_now(),
        )
        logger.debug(f"[CausalContext] 新建会话: {session_id}")

    cc = _cc_registry[session_id]
    # 每次获取时更新虚拟时间
    cc.update_virtual_time(_get_virtual_now())
    return cc


def remove_cc(session_id: str) -> None:
    """移除会话的 CausalContext（会话结束时调用）。"""
    _cc_registry.pop(session_id, None)


def get_cc_safe(session_id: str) -> Optional[CausalContext]:
    """安全获取 CausalContext，不存在时返回 None（不自动创建）。"""
    return _cc_registry.get(session_id)


def reset_all_cc() -> None:
    """重置所有 CausalContext（测试清理用）。"""
    _cc_registry.clear()
    global _virtual_time_provider
    _virtual_time_provider = None


def get_active_sessions() -> List[str]:
    """获取所有活跃会话 ID。"""
    return list(_cc_registry.keys())


def get_cc_count() -> int:
    """获取缓存的会话数。"""
    return len(_cc_registry)
