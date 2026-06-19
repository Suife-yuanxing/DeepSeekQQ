"""测试非语言信号检测器 — Phase 2.4"""

import time
import pytest
from plugins.deepseek.nonverbal_signals import (
    NonVerbalSignals,
    analyze_nonverbal,
    record_recall,
    get_recent_recalls,
    get_nonverbal_hint,
    _analyze_reply_gaps,
    _analyze_reply_length,
    _analyze_stickers,
    _analyze_questions,
    _detect_tone_shift,
    _compute_cold_shoulder,
    _count_stickers,
    _count_questions,
)


# ═══════════════════════════════════════
# Test: 数据结构
# ═══════════════════════════════════════

class TestNonVerbalSignals:
    def test_default_values(self):
        s = NonVerbalSignals()
        assert s.gap_trend == "stable"
        assert s.length_trend == "stable"
        assert s.sticker_change == "normal"
        assert s.question_change == "normal"
        assert s.tone_shift_detected is False
        assert s.recall_detected is False
        assert s.signal_count == 0
        assert s.cold_shoulder_score == 0.0

    def test_has_any_signal_empty(self):
        s = NonVerbalSignals()
        assert s.has_any_signal() is False

    def test_has_any_signal_gap(self):
        s = NonVerbalSignals(gap_trend="lengthening")
        assert s.has_any_signal() is True

    def test_has_any_signal_combo(self):
        s = NonVerbalSignals(length_trend="shortening", sticker_change="stopped")
        assert s.has_any_signal() is True

    def test_emotion_feedback_cold_shoulder_strong(self):
        s = NonVerbalSignals(cold_shoulder_score=0.7)
        fb = s.get_emotion_feedback()
        assert fb is not None
        assert fb["emotion"] == "被冷落"
        assert fb["intensity"] > 0.5

    def test_emotion_feedback_cold_shoulder_weak(self):
        s = NonVerbalSignals(cold_shoulder_score=0.3)
        fb = s.get_emotion_feedback()
        assert fb is None  # 低于0.6不触发

    def test_emotion_feedback_sticker_stop_len_short(self):
        s = NonVerbalSignals(sticker_change="stopped", length_trend="shortening")
        fb = s.get_emotion_feedback()
        assert fb is not None
        assert fb["emotion"] == "不安"

    def test_emotion_feedback_tone_shift_gap_len(self):
        s = NonVerbalSignals(
            tone_shift_detected=True,
            gap_trend="lengthening",
            tone_shift_detail="语气词消失",
        )
        fb = s.get_emotion_feedback()
        assert fb is not None
        assert fb["emotion"] == "担心"

    def test_emotion_feedback_gap_anomaly(self):
        s = NonVerbalSignals(gap_anomaly=True)
        fb = s.get_emotion_feedback()
        assert fb is not None
        assert fb["emotion"] == "被冷落"
        assert fb["intensity"] == 0.2


# ═══════════════════════════════════════
# Test: 回复间隔分析
# ═══════════════════════════════════════

class TestReplyGapAnalysis:
    def _make_msgs(self, timestamps):
        return [{"role": "user", "content": "test", "timestamp": ts} for ts in timestamps]

    def test_too_few_messages(self):
        msgs = self._make_msgs([100, 200, 300])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert trend == "stable"

    def test_stable_intervals(self):
        msgs = self._make_msgs([100, 200, 300, 400, 500, 600, 700])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert trend == "stable"

    def test_lengthening_trend(self):
        # 前半间隔小（5s），后半间隔大（50s）
        msgs = self._make_msgs([100, 105, 110, 115, 165, 215, 265])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert trend == "lengthening"

    def test_shortening_trend(self):
        # 前半间隔大，后半间隔小
        msgs = self._make_msgs([100, 160, 220, 280, 310, 315, 320])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert trend == "shortening"

    def test_anomaly_detection(self):
        now = time.time()
        # 前5条很短间隔（2s），最后一条间隔极大（3600s = 1小时）
        msgs = self._make_msgs([
            now - 3700, now - 3698, now - 3696, now - 3694,
            now - 3692, now - 20,  # 最后一条突然间隔~3672s，远超3σ
        ])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert anomaly is True

    def test_no_anomaly_on_normal(self):
        now = time.time()
        msgs = self._make_msgs([
            now - 50, now - 40, now - 30, now - 20, now - 10, now,
        ])
        avg, trend, anomaly = _analyze_reply_gaps(msgs)
        assert anomaly is False


# ═══════════════════════════════════════
# Test: 回复长度分析
# ═══════════════════════════════════════

class TestReplyLengthAnalysis:
    def _make_msgs(self, lengths):
        return [{"role": "user", "content": "x" * l, "timestamp": i} for i, l in enumerate(lengths)]

    def test_too_few_messages(self):
        msgs = self._make_msgs([10, 20, 30])
        avg, trend = _analyze_reply_length(msgs)
        assert trend == "stable"

    def test_stable_length(self):
        msgs = self._make_msgs([20, 22, 18, 25, 21, 19])
        avg, trend = _analyze_reply_length(msgs)
        assert trend == "stable"

    def test_shortening_trend(self):
        # 前半长（平均20），后半短（平均3）
        msgs = self._make_msgs([20, 22, 18, 5, 2, 2])
        avg, trend = _analyze_reply_length(msgs)
        assert trend == "shortening"

    def test_lengthening_trend(self):
        # 前半短，后半长
        msgs = self._make_msgs([3, 5, 2, 30, 35, 28])
        avg, trend = _analyze_reply_length(msgs)
        assert trend == "lengthening"


# ═══════════════════════════════════════
# Test: 表情包分析
# ═══════════════════════════════════════

class TestStickerAnalysis:
    def _make_msgs(self, contents):
        return [{"role": "user", "content": c, "timestamp": i} for i, c in enumerate(contents)]

    def test_normal_no_stickers(self):
        msgs = self._make_msgs(["hello"] * 8)
        freq, change = _analyze_stickers(msgs)
        assert change == "normal"

    def test_stopped_detection(self):
        # 前4条有表情，后4条没有
        msgs = self._make_msgs([
            "hello [CQ:image,file=xxx]", "hi [CQ:image,file=yyy]",
            "hey [CQ:image,file=zzz]", "yo [CQ:image,file=www]",
            "ok", "sure", "fine", "bye",
        ])
        freq, change = _analyze_stickers(msgs)
        assert change == "stopped"

    def test_increased_detection(self):
        msgs = self._make_msgs([
            "ok", "sure", "fine", "bye",
            "hello [CQ:image,file=xxx]", "hi [CQ:image,file=yyy]",
            "hey [CQ:image,file=zzz]", "yo",
        ])
        freq, change = _analyze_stickers(msgs)
        assert change == "increased"


# ═══════════════════════════════════════
# Test: 反问频率分析
# ═══════════════════════════════════════

class TestQuestionAnalysis:
    def _make_msgs(self, contents):
        return [{"role": "user", "content": c, "timestamp": i} for i, c in enumerate(contents)]

    def test_normal(self):
        msgs = self._make_msgs(["hello", "world"] * 4)
        freq, change = _analyze_questions(msgs)
        assert change == "normal"

    def test_declined_questions(self):
        # 前4条有反问，后4条没有
        msgs = self._make_msgs([
            "你觉得呢？", "怎么办？", "好不好？", "为什么？",
            "好的", "知道了", "行", "嗯",
        ])
        freq, change = _analyze_questions(msgs)
        assert change == "declined"

    def test_increased_questions(self):
        msgs = self._make_msgs([
            "嗯", "好的", "行", "知道了",
            "你怎么看？", "是不是这样？", "怎么办？", "为什么呢？",
        ])
        freq, change = _analyze_questions(msgs)
        assert change == "increased"


# ═══════════════════════════════════════
# Test: 语气突变
# ═══════════════════════════════════════

class TestToneShift:
    def _make_msgs(self, contents):
        return [{"role": "user", "content": c, "timestamp": i} for i, c in enumerate(contents)]

    def test_no_shift(self):
        msgs = self._make_msgs(["hello", "world"] * 4)
        detected, detail = _detect_tone_shift(msgs)
        assert detected is False

    def test_laughter_disappearance(self):
        msgs = self._make_msgs([
            "哈哈哈笑死", "笑死我了哈哈哈", "www草", "哈哈真的吗",
            "嗯", "好的", "知道了", "行",
        ])
        detected, detail = _detect_tone_shift(msgs)
        assert detected is True
        assert "消失" in detail

    def test_laughter_appearance(self):
        msgs = self._make_msgs([
            "嗯", "好的", "知道了", "行",
            "哈哈哈笑死", "笑死我了哈哈哈", "www", "哈哈",
        ])
        detected, detail = _detect_tone_shift(msgs)
        assert detected is True


# ═══════════════════════════════════════
# Test: 撤回
# ═══════════════════════════════════════

class TestRecall:
    def test_no_recall(self):
        assert get_recent_recalls("test_session") == 0

    def test_record_and_get(self):
        record_recall("test_session2")
        assert get_recent_recalls("test_session2") >= 1

    def test_old_recalls_expire(self):
        # 直接修改内部存储来模拟过期
        from plugins.deepseek.nonverbal_signals import _session_recalls
        old_time = time.time() - 400  # 超过5分钟
        _session_recalls["test_expired"] = [old_time]
        assert get_recent_recalls("test_expired") == 0


# ═══════════════════════════════════════
# Test: 冷落得分
# ═══════════════════════════════════════

class TestColdShoulder:
    def test_no_signals(self):
        s = NonVerbalSignals()
        score = _compute_cold_shoulder(s)
        assert score == 0.0

    def test_full_cold_shoulder(self):
        s = NonVerbalSignals(
            gap_trend="lengthening",
            length_trend="shortening",
            sticker_change="stopped",
            question_change="declined",
            gap_anomaly=True,
            tone_shift_detected=True,
            recall_detected=True,
        )
        score = _compute_cold_shoulder(s)
        assert score > 0.6  # 多信号组合得分高

    def test_partial_cold_shoulder(self):
        s = NonVerbalSignals(gap_trend="lengthening", length_trend="shortening")
        score = _compute_cold_shoulder(s)
        assert 0.4 < score < 0.7


# ═══════════════════════════════════════
# Test: 主分析函数
# ═══════════════════════════════════════

class TestAnalyzeNonverbal:
    def test_empty_memories(self):
        result = analyze_nonverbal("test", [])
        assert result.has_any_signal() is False

    def test_normal_conversation(self):
        now = time.time()
        msgs = [
            {"role": "user", "content": "你好呀", "timestamp": now - 50},
            {"role": "bot", "content": "嗨~", "timestamp": now - 45},
            {"role": "user", "content": "今天天气不错", "timestamp": now - 40},
            {"role": "bot", "content": "是啊", "timestamp": now - 35},
            {"role": "user", "content": "去公园逛逛", "timestamp": now - 30},
            {"role": "bot", "content": "好主意", "timestamp": now - 25},
            {"role": "user", "content": "你最近怎么样", "timestamp": now - 20},
            {"role": "bot", "content": "挺好的", "timestamp": now - 15},
            {"role": "user", "content": "那就好", "timestamp": now - 10},
            {"role": "bot", "content": "嗯嗯", "timestamp": now - 5},
        ]
        result = analyze_nonverbal("test", msgs)
        # 正常对话应该稳定
        assert result.gap_trend == "stable"
        assert result.length_trend == "stable"

    def test_cold_conversation_signals(self):
        now = time.time()
        msgs = [
            {"role": "user", "content": "你好呀,好久不见 [CQ:image,file=xxx]", "timestamp": now - 500},
            {"role": "user", "content": "最近怎么样？有什么好玩的事吗？", "timestamp": now - 480},
            {"role": "user", "content": "哈哈真的吗 [CQ:image,file=yyy]", "timestamp": now - 460},
            {"role": "user", "content": "你觉得呢？是不是很有意思？", "timestamp": now - 440},
            {"role": "user", "content": "嗯", "timestamp": now - 100},
            {"role": "user", "content": "好吧", "timestamp": now - 50},
            {"role": "user", "content": "行", "timestamp": now - 20},
            {"role": "user", "content": "嗯", "timestamp": now - 5},
        ]
        result = analyze_nonverbal("test", msgs)
        # 应该检测到信号变化
        assert result.length_trend == "shortening"  # 从长变短
        assert result.gap_trend == "lengthening"    # 间隔明显拉长（后半的间隔其实不大）
        # 冷落得分应该>0
        assert result.cold_shoulder_score > 0

    def test_signal_count_tracks_anomalies(self):
        """signal_count 在 analyze_nonverbal() 中计算"""
        now = time.time()
        # 构造有明显信号的消息序列
        msgs = [
            {"role": "user", "content": "嗯", "timestamp": now - 200},
            {"role": "user", "content": "哦", "timestamp": now - 100},
            {"role": "user", "content": "行", "timestamp": now - 50},
            {"role": "user", "content": "好的", "timestamp": now - 30},
            {"role": "user", "content": "嗯", "timestamp": now - 20},
            {"role": "user", "content": "哦", "timestamp": now - 10},
            {"role": "user", "content": "好吧", "timestamp": now - 5},
        ]
        result = analyze_nonverbal("test_sigcount", msgs)
        # 至少有 shortening 趋势信号
        assert result.signal_count >= 1


# ═══════════════════════════════════════
# Test: prompt 提示生成
# ═══════════════════════════════════════

class TestNonverbalHint:
    def test_no_signals(self):
        s = NonVerbalSignals()
        hint = get_nonverbal_hint(s)
        assert hint == ""

    def test_with_signals(self):
        s = NonVerbalSignals(
            gap_trend="lengthening",
            length_trend="shortening",
            cold_shoulder_score=0.5,
        )
        hint = get_nonverbal_hint(s)
        assert "间隔" in hint or "冷" in hint or "短" in hint
        assert len(hint) > 0


# ═══════════════════════════════════════
# Test: 辅助函数
# ═══════════════════════════════════════

class TestHelpers:
    def test_count_stickers_cqimage(self):
        assert _count_stickers("[CQ:image,file=xxx]") >= 1

    def test_count_stickers_no_sticker(self):
        assert _count_stickers("你好") == 0

    def test_count_questions_question_mark(self):
        assert _count_questions("你觉得呢？") >= 1

    def test_count_questions_no_question(self):
        assert _count_questions("知道了") == 0
