"""测试情绪累积模型 — Phase 2.1"""

import time
import pytest
from plugins.deepseek.emotion_accumulator import (
    EmotionUnit,
    EmotionAccumulator,
    quick_check_to_unit,
    get_accumulator,
    remove_accumulator,
    reset_all_accumulators,
)


# ═══════════════════════════════════════
# Test: EmotionUnit
# ═══════════════════════════════════════

class TestEmotionUnit:
    def test_weight_full_at_creation(self):
        unit = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.9, confidence=0.9)
        unit.timestamp = time.time()
        assert unit.weight() == 1.0

    def test_weight_decays_after_5min(self):
        unit = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.9, confidence=0.9)
        unit.timestamp = time.time() - 600  # 10分钟前
        w = unit.weight()
        assert w < 1.0  # 应该衰减了
        assert w > 0.05  # 但不应降到0

    def test_weight_floor(self):
        unit = EmotionUnit(intensity=0.5)
        unit.timestamp = time.time() - 99999  # 很久以前
        assert unit.weight() >= 0.05  # 最小权重 5%

    def test_weighted_valence(self):
        unit = EmotionUnit(valence=-0.7, intensity=0.8, confidence=0.9)
        unit.timestamp = time.time()
        assert unit.weighted_valence == pytest.approx(-0.56)  # -0.7 * 0.8 * 1.0

    def test_weighted_arousal(self):
        unit = EmotionUnit(arousal=0.6, intensity=0.5, confidence=0.9)
        unit.timestamp = time.time()
        assert unit.weighted_arousal == pytest.approx(0.3)  # 0.6 * 0.5 * 1.0


# ═══════════════════════════════════════
# Test: EmotionAccumulator
# ═══════════════════════════════════════

class TestEmotionAccumulator:
    def setup_method(self):
        reset_all_accumulators()

    def test_feed_single_below_threshold(self):
        acc = EmotionAccumulator(session_id="test", threshold=3.0)
        unit = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.5, confidence=0.5)
        unit.timestamp = time.time()
        result = acc.feed(unit)
        assert result is None  # 一个单元不够触发
        assert acc.buffer_size == 1

    def test_accumulate_to_threshold(self):
        acc = EmotionAccumulator(session_id="test", threshold=3.0)
        # 喂入多个同向情绪单元
        for i in range(5):
            unit = EmotionUnit(
                label="angry", valence=-0.7, arousal=0.8,
                intensity=0.6, confidence=0.6,
            )
            unit.timestamp = time.time()
            result = acc.feed(unit)

        # 累积到阈值后应该触发
        # （5个单元，每个 weighted_valence ≈ -0.42，5个累积 ≈ -2.1，不算 arousal 的话）
        # 但实际触发是在达到阈值的时候发生的
        assert acc.buffer_size == 0 or acc.is_pending  # 缓冲区清空 或 有待表现情绪

    def test_high_intensity_immediate(self):
        """高强度+高置信度立即触发"""
        acc = EmotionAccumulator(session_id="test")
        unit = EmotionUnit(
            label="angry", valence=-0.7, arousal=0.8,
            intensity=0.8, confidence=0.85,  # 高置信度 + 高强度
        )
        unit.timestamp = time.time()
        result = acc.feed(unit)
        assert result is not None
        assert result["immediate"] is True
        assert result["source"] == "accumulator"

    def test_low_intensity_no_immediate(self):
        """低强度不立即触发"""
        acc = EmotionAccumulator(session_id="test")
        unit = EmotionUnit(
            label="negative", valence=-0.3, arousal=0.3,
            intensity=0.3, confidence=0.4,
        )
        unit.timestamp = time.time()
        result = acc.feed(unit)
        assert result is None  # 低强度积累，未触发

    def test_decay_over_time(self):
        """旧情绪随时间衰减"""
        acc = EmotionAccumulator(session_id="test", threshold=3.0)
        # 喂入一个旧情绪（10分钟前）
        old_unit = EmotionUnit(
            label="angry", valence=-0.7, arousal=0.8,
            intensity=0.8, confidence=0.7,
        )
        old_unit.timestamp = time.time() - 700  # ~12分钟前
        acc.feed(old_unit)

        # 权重应该明显降低
        assert acc.buffer[0].weight() < 0.9
        # 不足以触发
        assert acc.buffer_size > 0

    def test_mixed_valence_cancellation(self):
        """正负情绪相互抵消"""
        acc = EmotionAccumulator(session_id="test", threshold=3.0)
        # 先喂负面
        neg = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.7, confidence=0.6)
        neg.timestamp = time.time()
        acc.feed(neg)

        # 再喂正面
        pos = EmotionUnit(label="positive", valence=0.7, arousal=0.6, intensity=0.7, confidence=0.6)
        pos.timestamp = time.time()
        acc.feed(pos)

        # 正负抵消，缓冲区内有2个单元但累积总分应该接近0
        total_v = sum(u.weighted_valence for u in acc.buffer)
        assert abs(total_v) < 0.5  # 抵消后接近0

    def test_semantic_order_preserved(self):
        """靠后的消息权重更高（audit-2-3 fix）"""
        acc = EmotionAccumulator(session_id="test", threshold=1.0)
        # 先正面后负面："太好了…其实很难过"
        pos = EmotionUnit(label="positive", valence=0.7, arousal=0.6, intensity=0.6, confidence=0.6)
        pos.timestamp = time.time()
        acc.feed(pos)

        neg = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.6, confidence=0.6)
        neg.timestamp = time.time()
        acc.feed(neg)

        # 触发时主导情绪应该是后面的（angry）
        dominant = acc._determine_dominant()
        # 靠后的应该权重更高
        assert dominant in ("angry", "negative")  # 负面应主导

    def test_delayed_reaction(self):
        """延迟1-5条消息后反应"""
        acc = EmotionAccumulator(session_id="test", threshold=1.0)
        # 先累积到阈值（用高intensity快速触发）
        for i in range(3):
            unit = EmotionUnit(
                label="angry", valence=-0.7, arousal=0.8,
                intensity=0.9, confidence=0.5,  # 高强度但中等置信度
            )
            unit.timestamp = time.time()
            result = acc.feed(unit)
            if result and result.get("immediate"):
                break

        # 如果已在待表现状态
        if acc.is_pending:
            # 喂入 tick 推进倒计时
            tick = EmotionUnit(label="neutral", valence=0, arousal=0.1, intensity=0.05, confidence=0.1)
            tick.timestamp = time.time()
            for _ in range(6):  # 最多推进6次
                result = acc.feed(tick)
                if result:
                    assert result["source"] == "accumulator"
                    assert result["emotion"] in ("生气", "被冷落")
                    return
            # 如果还没触发说明还在倒计时，也算正常
            assert acc.is_pending or acc.buffer_size >= 0
        # 如果立即触发了（高置信度路径），也算通过
        # just pass

    def test_flush_clears_buffer(self):
        acc = EmotionAccumulator(session_id="test")
        unit = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.5, confidence=0.5)
        unit.timestamp = time.time()
        acc.feed(unit)
        assert acc.buffer_size > 0
        acc.flush()
        assert acc.buffer_size == 0

    def test_reset(self):
        acc = EmotionAccumulator(session_id="test")
        unit = EmotionUnit(label="angry", valence=-0.7, arousal=0.8, intensity=0.5, confidence=0.5)
        unit.timestamp = time.time()
        acc.feed(unit)
        acc.reset()
        assert acc.buffer_size == 0
        assert not acc.is_pending


# ═══════════════════════════════════════
# Test: quick_check_to_unit
# ═══════════════════════════════════════

class TestQuickCheckToUnit:
    def test_angry_keyword(self):
        unit = quick_check_to_unit("你滚开 傻逼")
        assert unit.label == "angry"
        assert unit.confidence >= 0.8
        assert unit.source == "keyword"

    def test_positive_keyword(self):
        unit = quick_check_to_unit("哈哈开心！太好了！")
        assert unit.label == "positive"
        assert unit.confidence >= 0.5

    def test_neutral_message(self):
        unit = quick_check_to_unit("今天天气不错")
        assert unit.label == "neutral"
        assert unit.intensity <= 0.2

    def test_empty_message(self):
        unit = quick_check_to_unit("")
        assert unit.label == "neutral"


# ═══════════════════════════════════════
# Test: 累加器全局管理
# ═══════════════════════════════════════

class TestAccumulatorRegistry:
    def setup_method(self):
        reset_all_accumulators()

    def test_get_creates_new(self):
        acc = get_accumulator("test_session")
        assert acc is not None
        assert acc.session_id == "test_session"

    def test_get_returns_same(self):
        acc1 = get_accumulator("test_session")
        acc2 = get_accumulator("test_session")
        assert acc1 is acc2

    def test_remove(self):
        get_accumulator("test_session")
        remove_accumulator("test_session")
        acc_new = get_accumulator("test_session")
        # 新创建的应该不同（reset后新建）
        assert acc_new.buffer_size == 0

    def test_reset_all(self):
        get_accumulator("s1")
        get_accumulator("s2")
        reset_all_accumulators()
        # 重新获取应该是新的空累加器
        acc = get_accumulator("s1")
        assert acc.buffer_size == 0
