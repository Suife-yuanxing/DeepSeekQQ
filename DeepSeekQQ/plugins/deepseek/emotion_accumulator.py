"""情绪累积模型 — 替代单条消息关键词触发情绪。

真人化 Phase 2.1 核心模块：
- 每条消息打分（情绪倾向 + 强度），不再立即切换情绪
- 关键词匹配降级为「快速预判」，只产出 EmotionUnit 不直接切换状态
- 累积到阈值才触发情绪切换 → 消除 audit-2-1 的双重计算
- 累积过程保留语义顺序 → 消除 audit-2-3 的顺序无视
- 延迟反应：隔 1-5 条消息才表现（事后反刍）

设计要点：
- 高强度消息（如直接被骂）仍可以立即触发
- 低强度情绪需要累积 3+ 条同向消息
- 正负混合消息按语义顺序处理（靠后消息权重更高）
- 旧情绪单位随时间衰减（10分钟前的权重降低 50%+）
"""

import random as _random
import time as _time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger

from .config import HUMANIZE_TUNING_EMOTION_ACCUMULATOR_THRESHOLD as _ACCUMULATOR_THRESHOLD


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass
class EmotionUnit:
    """单条消息的情绪单元。"""

    label: str = ""             # 情绪标签 (angry, positive, negative, excited, etc.)
    valence: float = 0.0        # 效价 -1.0 ~ 1.0
    arousal: float = 0.0        # 唤醒度 0.0 ~ 1.0
    intensity: float = 0.0      # 强度 0.0 ~ 1.0
    confidence: float = 0.0     # 置信度 0.0 ~ 1.0
    timestamp: float = 0.0      # 创建时间 time.time()
    source: str = "keyword"     # keyword / llm

    def weight(self, now: float = None) -> float:
        """计算当前权重（考虑时间衰减）。

        时间衰减：超过 5 分钟开始衰减，每 5 分钟衰减 25%。
        """
        if now is None:
            now = _time.time()
        age = now - self.timestamp
        if age <= 300:  # 5分钟内全权重
            return 1.0
        # 每5分钟衰减25%
        periods = (age - 300) / 300
        decay = 0.75 ** periods
        return max(0.05, decay)

    @property
    def weighted_valence(self) -> float:
        return self.valence * self.intensity * self.weight()

    @property
    def weighted_arousal(self) -> float:
        return self.arousal * self.intensity * self.weight()


@dataclass
class EmotionAccumulator:
    """情绪累积缓冲区 — 每个会话一个实例。

    不直接修改全局情绪状态，而是累积到阈值后产出触发结果。
    """

    session_id: str = ""
    threshold: float = field(default_factory=lambda: _ACCUMULATOR_THRESHOLD)  # 累积触发阈值（可配置）
    buffer: List[EmotionUnit] = field(default_factory=list)
    _triggered_emotion: Optional[str] = None  # 已触发但未表现的情绪
    _trigger_countdown: int = 0              # 延迟计数器（0=立即，1-5=延迟）
    _last_flush: float = 0.0                 # 上次清空缓冲区时间

    def feed(self, unit: EmotionUnit) -> Optional[Dict[str, Any]]:
        """喂入一个情绪单元。

        高置信度/高强度情绪可能立即触发；
        普通情绪需要累积到阈值。

        Returns:
            None（未触发）或 {"emotion": str, "intensity": float, "valence": float, "arousal": float}
        """
        now = _time.time()
        # 仅当 timestamp 未设置时设为当前时间（允许测试构造历史单元）
        if not unit.timestamp:
            unit.timestamp = now

        # 高置信度 + 高强度 → 立即触发（被直接骂了之类的）
        if unit.confidence >= 0.8 and unit.intensity >= 0.7:
            self.buffer.clear()
            self._triggered_emotion = None
            self._trigger_countdown = 0
            logger.info(
                f"[情绪累积] 高强度立即触发: {unit.label} "
                f"(intensity={unit.intensity:.2f}, conf={unit.confidence:.2f})"
            )
            return self._build_result(unit, immediate=True)

        # 清理过期单元
        self._prune_stale()

        # 加入缓冲区
        self.buffer.append(unit)
        # 限制缓冲区大小
        if len(self.buffer) > 20:
            self.buffer = self.buffer[-20:]

        # 计算累积分数
        total_valence = sum(u.weighted_valence for u in self.buffer)
        total_arousal = sum(u.weighted_arousal for u in self.buffer)
        abs_total = abs(total_valence) + abs(total_arousal) * 0.5

        logger.debug(
            f"[情绪累积] session={self.session_id[:8]} "
            f"buffer={len(self.buffer)} total={abs_total:.2f}/{self.threshold} "
            f"last={unit.label}(+{unit.intensity:.1f})"
        )

        # 检查是否达到触发阈值
        if abs_total >= self.threshold:
            # 确定触发情绪类型
            dominant_label = self._determine_dominant()
            # 设置延迟计数器（1-5条消息后表现）
            self._triggered_emotion = dominant_label
            self._trigger_countdown = _random.randint(1, 5)
            self.buffer.clear()

            logger.info(
                f"[情绪累积] 达到阈值触发: {dominant_label} "
                f"(total={abs_total:.2f}, countdown={self._trigger_countdown})"
            )

        # 检查是否有待表现的延迟情绪
        if self._triggered_emotion and self._trigger_countdown <= 0:
            emotion = self._triggered_emotion
            self._triggered_emotion = None
            result = self._build_result_from_label(emotion)
            logger.info(f"[情绪累积] 延迟情绪表现: {emotion}")
            return result

        if self._triggered_emotion:
            self._trigger_countdown -= 1
            logger.debug(
                f"[情绪累积] 延迟倒计时: {self._triggered_emotion} "
                f"countdown={self._trigger_countdown}"
            )

        return None

    def _prune_stale(self) -> None:
        """清理权重已降到极低的过期单元。"""
        now = _time.time()
        self.buffer = [u for u in self.buffer if u.weight(now) > 0.1]

    def _determine_dominant(self) -> str:
        """根据缓冲区确定主导情绪标签。

        语义顺序保留：靠后的消息权重更高（权重因子 1.5）。
        """
        if not self.buffer:
            return "平静"

        # 按标签分组，计算加权总分
        labels: Dict[str, float] = {}
        n = len(self.buffer)
        for i, unit in enumerate(self.buffer):
            pos_weight = 1.0 + (i / n) * 1.5  # 位置权重：后面消息×1.0~2.5
            score = abs(unit.weighted_valence) * pos_weight
            label = unit.label
            labels[label] = labels.get(label, 0.0) + score

        if not labels:
            return "平静"

        return max(labels, key=labels.get)

    def _build_result(
        self, unit: EmotionUnit, immediate: bool = False
    ) -> Dict[str, Any]:
        """从 EmotionUnit 构建触发结果。"""
        label_map = {
            "angry": ("生气", -0.7, 0.8),
            "negative": ("被冷落", -0.5, 0.4),
            "positive": ("开心", 0.6, 0.5),
            "excited": ("兴奋", 0.7, 0.7),
            "shy": ("害羞", 0.4, 0.6),
            "anxious": ("担心", -0.4, 0.55),
            "sad": ("难过", -0.6, 0.3),
        }
        emotion, valence, arousal = label_map.get(
            unit.label, ("平静", unit.valence, unit.arousal)
        )
        return {
            "emotion": emotion,
            "intensity": unit.intensity,
            "valence": valence,
            "arousal": arousal,
            "immediate": immediate,
            "source": "accumulator",
        }

    def _build_result_from_label(self, label: str) -> Dict[str, Any]:
        """从情绪标签构建触发结果。"""
        fake_unit = EmotionUnit(label=label, intensity=0.5, confidence=0.6)
        return self._build_result(fake_unit, immediate=False)

    def flush(self) -> Optional[Dict[str, Any]]:
        """强制清空缓冲区并返回累积结果（对话结束时调用）。"""
        if not self.buffer and not self._triggered_emotion:
            return None

        if self._triggered_emotion:
            emotion = self._triggered_emotion
            self._triggered_emotion = None
            self._trigger_countdown = 0
            self.buffer.clear()
            return self._build_result_from_label(emotion)

        dominant = self._determine_dominant()
        self.buffer.clear()
        if dominant == "平静":
            return None
        return self._build_result_from_label(dominant)

    def reset(self) -> None:
        """重置缓冲区（测试用）。"""
        self.buffer.clear()
        self._triggered_emotion = None
        self._trigger_countdown = 0

    @property
    def is_pending(self) -> bool:
        """是否有待表现的情绪。"""
        return self._triggered_emotion is not None

    @property
    def buffer_size(self) -> int:
        return len(self.buffer)


# ═══════════════════════════════════════════════════════════════
# 标签 → VA 映射
# ═══════════════════════════════════════════════════════════════

_LABEL_TO_VA: Dict[str, tuple] = {
    "angry": (-0.7, 0.8),
    "anxious": (-0.4, 0.55),
    "positive": (0.6, 0.5),
    "negative": (-0.5, 0.4),
    "excited": (0.7, 0.7),
    "shy": (0.4, 0.6),
    "neutral": (0.0, 0.2),
}


def quick_check_to_unit(text: str) -> EmotionUnit:
    """将 quick_emotion_check 的结果转换为 EmotionUnit。

    这是 emotion_classifier 和 emotion_accumulator 之间的桥梁。
    关键词匹配降级为「仅产出 EmotionUnit，不直接切换状态」。
    """
    from .emotion_classifier import quick_emotion_check

    label, confidence = quick_emotion_check(text)

    if label is None or confidence < 0.3:
        return EmotionUnit(
            label="neutral",
            valence=0.0,
            arousal=0.2,
            intensity=0.1,
            confidence=0.1,
            source="keyword",
        )

    valence, arousal = _LABEL_TO_VA.get(label, (0.0, 0.2))
    intensity = min(1.0, confidence)  # 强度 = 置信度

    return EmotionUnit(
        label=label,
        valence=valence,
        arousal=arousal,
        intensity=intensity,
        confidence=confidence,
        source="keyword",
    )


# ═══════════════════════════════════════════════════════════════
# 全局累加器管理
# ═══════════════════════════════════════════════════════════════

_accumulators: Dict[str, EmotionAccumulator] = {}
_MAX_ACCUMULATORS = 200


def get_accumulator(session_id: str) -> EmotionAccumulator:
    """获取或创建会话的情绪累积器。"""
    if session_id not in _accumulators:
        if len(_accumulators) >= _MAX_ACCUMULATORS:
            oldest = next(iter(_accumulators))
            del _accumulators[oldest]
        _accumulators[session_id] = EmotionAccumulator(session_id=session_id)
    return _accumulators[session_id]


def remove_accumulator(session_id: str) -> None:
    """移除会话的累积器。"""
    _accumulators.pop(session_id, None)


def reset_all_accumulators() -> None:
    """重置所有累积器（测试用）。"""
    _accumulators.clear()
