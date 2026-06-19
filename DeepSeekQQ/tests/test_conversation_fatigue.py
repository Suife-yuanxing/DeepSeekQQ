"""测试对话疲劳感知功能。"""
import time
import pytest
from unittest.mock import MagicMock
from plugins.deepseek.conversation_fatigue import (
    analyze_conversation_fatigue,
    get_closing_message,
    _detect_closing_words,
    _detect_message_shortening,
    _build_fatigue_hint,
)

pytestmark = [pytest.mark.unit]


class TestClosingWordsDetection:
    """收尾词检测测试。"""

    def test_single_closing_word(self):
        """单条收尾词给低分。"""
        msgs = [{"role": "user", "content": "聊点别的吧", "timestamp": 1}] * 5
        result = _detect_closing_words("嗯", msgs)
        assert result == 1.0

    def test_consecutive_closing_words(self):
        """连续收尾词给高分。"""
        msgs = [
            {"role": "user", "content": "嗯", "timestamp": 1},
            {"role": "user", "content": "好的", "timestamp": 2},
            {"role": "user", "content": "行", "timestamp": 3},
            {"role": "user", "content": "哦", "timestamp": 4},
        ]
        result = _detect_closing_words("嗯嗯", msgs)
        assert result >= 2.5

    def test_non_closing_word(self):
        """非收尾词不触发。"""
        msgs = [{"role": "user", "content": "今天天气真好", "timestamp": 1}]
        result = _detect_closing_words("是啊确实不错", msgs)
        assert result == 0.0


class TestMessageShortening:
    """消息变短检测测试。"""

    def test_messages_getting_shorter(self):
        """消息明显变短。"""
        msgs = [
            {"role": "user", "content": "今天去了一个特别好玩的地方，风景超级好看", "timestamp": 1},
            {"role": "user", "content": "还吃了好多好吃的东西", "timestamp": 2},
            {"role": "user", "content": "真的很开心啊", "timestamp": 3},
            {"role": "user", "content": "嗯", "timestamp": 4},
            {"role": "user", "content": "好", "timestamp": 5},
            {"role": "user", "content": "哦", "timestamp": 6},
        ]
        result = _detect_message_shortening(msgs)
        assert result > 0

    def test_messages_stable_length(self):
        """消息长度稳定不触发。"""
        msgs = [
            {"role": "user", "content": "今天天气不错啊", "timestamp": i}
            for i in range(8)
        ]
        result = _detect_message_shortening(msgs)
        assert result == 0.0

    def test_too_few_messages(self):
        """消息太少不触发。"""
        msgs = [{"role": "user", "content": "嗯", "timestamp": 1}]
        result = _detect_message_shortening(msgs)
        assert result == 0.0


class TestAnalyzeConversationFatigue:
    """整体疲劳分析测试。"""

    def test_normal_conversation(self):
        """正常对话疲劳等级为 0。"""
        schedule = MagicMock()
        schedule.period = "active"
        msgs = [
            {"role": "user", "content": f"今天第{i}条消息，聊点有意思的话题吧", "timestamp": i * 60}
            for i in range(1, 8)
        ]
        result = analyze_conversation_fatigue(msgs, "你觉得呢？", schedule)
        assert result["level"] == 0
        assert result["hint"] == ""

    def test_closing_words_trigger(self):
        """连续收尾词触发疲劳。"""
        schedule = MagicMock()
        schedule.period = "active"
        msgs = [
            {"role": "user", "content": "嗯", "timestamp": 1},
            {"role": "user", "content": "好的", "timestamp": 2},
            {"role": "user", "content": "行", "timestamp": 3},
        ]
        result = analyze_conversation_fatigue(msgs, "哦", schedule)
        assert result["level"] >= 1

    def test_late_night_boosts_fatigue(self):
        """深夜时段提升疲劳分。"""
        schedule = MagicMock()
        schedule.period = "sleeping"
        msgs = [
            {"role": "user", "content": f"消息{i}", "timestamp": i * 60}
            for i in range(1, 5)
        ]
        result = analyze_conversation_fatigue(msgs, "嗯", schedule)
        assert result["score"] >= 3.0  # 深夜(3) + 收尾词(1)

    def test_strong_closing_keyword(self):
        """强收尾关键词直接拉高分。"""
        schedule = MagicMock()
        schedule.period = "active"
        msgs = []
        result = analyze_conversation_fatigue(msgs, "晚安", schedule)
        assert result["level"] >= 2
        assert result["signals"].get("strong_closing", 0) > 0

    def test_combined_signals(self):
        """多信号叠加。"""
        schedule = MagicMock()
        schedule.period = "night_owl"
        msgs = [
            {"role": "user", "content": "很长的一条消息，聊了很多内容呢", "timestamp": 1},
            {"role": "user", "content": "还行吧", "timestamp": 2},
            {"role": "user", "content": "嗯", "timestamp": 3},
            {"role": "user", "content": "好的", "timestamp": 4},
        ]
        result = analyze_conversation_fatigue(msgs, "行", schedule)
        assert result["level"] >= 1

    def test_returns_dict_structure(self):
        """返回值结构正确。"""
        schedule = MagicMock()
        schedule.period = "active"
        result = analyze_conversation_fatigue([], "你好", schedule)
        assert "level" in result
        assert "hint" in result
        assert "score" in result
        assert "signals" in result


class TestFatigueHint:
    """疲劳提示文本测试。"""

    def test_level_0_empty(self):
        assert _build_fatigue_hint(0, {}) == ""

    def test_level_1_no_questions(self):
        hint = _build_fatigue_hint(1, {})
        assert "不要" in hint or "不主动" in hint

    def test_level_2_closing(self):
        hint = _build_fatigue_hint(2, {})
        assert "收尾" in hint or "结束" in hint

    def test_level_3_strong_closing(self):
        hint = _build_fatigue_hint(3, {})
        assert "结束" in hint or "告别" in hint or "收尾" in hint


class TestClosingMessage:
    """收尾消息测试。"""

    def test_level_0_no_message(self):
        assert get_closing_message(0) is None

    def test_level_1_no_message(self):
        assert get_closing_message(1) is None

    def test_level_2_no_message(self):
        """level 2 由 LLM 自然收尾，不追加消息。"""
        assert get_closing_message(2) is None

    def test_level_3_has_message(self):
        msg = get_closing_message(3)
        assert msg is not None
        assert len(msg) > 0

    def test_level_3_late_night(self):
        schedule = MagicMock()
        schedule.period = "sleeping"
        msg = get_closing_message(3, schedule)
        assert msg is not None
        assert "休息" in msg or "睡" in msg or "晚安" in msg

    def test_level_3_normal_time(self):
        schedule = MagicMock()
        schedule.period = "active"
        msg = get_closing_message(3, schedule)
        assert msg is not None


class TestPromptInjection:
    """测试 prompt 注入。"""

    def test_fatigue_hint_in_prompt(self):
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="嗯",
            fatigue_hint="用户可能有点聊累了。回复简短一些。",
        )
        assert "对话节奏" in prompt
        assert "聊累了" in prompt

    def test_no_fatigue_hint_when_empty(self):
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="你好",
            fatigue_hint=None,
        )
        assert "对话节奏" not in prompt


# ============================================================
# 真人化 P2-2：基线学习 + 忙/烦区分 + 加权相关系数
# ============================================================

class TestBaselineLearning:
    """用户回复风格基线学习测试"""

    def test_shortening_vs_baseline_below_half(self):
        """当前回复长度 < 基线 50% → 应检测到变短"""
        from plugins.deepseek.conversation_fatigue import _detect_shortening_vs_baseline

        user_msgs = [
            {"role": "user", "content": "哦", "timestamp": i}
            for i in range(6)
        ]
        baseline = {"avg_reply_length": 20.0, "sample_count": 30}
        score = _detect_shortening_vs_baseline(user_msgs, baseline)
        assert score > 0, f"应检测到回复明显短于基线，得分={score}"

    def test_shortening_vs_baseline_normal(self):
        """当前回复长度接近基线 → 不触发"""
        from plugins.deepseek.conversation_fatigue import _detect_shortening_vs_baseline

        user_msgs = [
            {"role": "user", "content": "今天天气真不错啊适合出门走走", "timestamp": i}
            for i in range(6)
        ]
        baseline = {"avg_reply_length": 15.0, "sample_count": 30}
        score = _detect_shortening_vs_baseline(user_msgs, baseline)
        assert score == 0.0, f"回复长度正常不应触发，得分={score}"

    def test_slowdown_vs_baseline(self):
        """回复间隔 > 2× 基线 → 应检测到变慢"""
        from plugins.deepseek.conversation_fatigue import _detect_slowdown_vs_baseline

        user_msgs = [
            {"role": "user", "content": f"msg{i}", "timestamp": i * 300}
            for i in range(6)
        ]
        baseline = {"avg_reply_gap": 60.0, "sample_count": 30}
        score = _detect_slowdown_vs_baseline(user_msgs, baseline)
        assert score > 0, f"应检测到回复明显慢于基线，得分={score}"

    def test_slowdown_vs_baseline_normal(self):
        """回复间隔接近基线 → 不触发"""
        from plugins.deepseek.conversation_fatigue import _detect_slowdown_vs_baseline

        user_msgs = [
            {"role": "user", "content": f"msg{i}", "timestamp": i * 20}
            for i in range(6)
        ]
        baseline = {"avg_reply_gap": 40.0, "sample_count": 30}
        score = _detect_slowdown_vs_baseline(user_msgs, baseline)
        assert score == 0.0, f"回复速度正常不应触发，得分={score}"

    def test_insufficient_samples_no_baseline(self):
        """样本数不足（<20）→ 使用绝对阈值而非基线"""
        from plugins.deepseek.conversation_fatigue import has_sufficient_baseline

        assert not has_sufficient_baseline({"sample_count": 5, "avg_reply_length": 10})
        assert not has_sufficient_baseline({"sample_count": 19, "avg_reply_length": 10})
        assert has_sufficient_baseline({"sample_count": 20, "avg_reply_length": 10})
        assert has_sufficient_baseline({"sample_count": 100, "avg_reply_length": 10})


class TestFatigueTypeClassification:
    """忙/烦区分测试（真人化 P2-2）"""

    def test_slowdown_no_shortening_is_busy(self):
        """间隔拉长但回复不短 → 忙"""
        from plugins.deepseek.conversation_fatigue import _classify_fatigue_type

        signals = {"reply_slowdown": 1.0, "message_shortening": 0.0}
        result = _classify_fatigue_type(level=2, signals=signals, has_baseline=True)
        assert result == "忙", f"应判断为'忙'，实际={result}"

    def test_slowdown_and_shortening_is_annoyed(self):
        """间隔拉长 + 回复变短 → 烦"""
        from plugins.deepseek.conversation_fatigue import _classify_fatigue_type

        signals = {"reply_slowdown": 1.0, "message_shortening": 1.0}
        result = _classify_fatigue_type(level=2, signals=signals, has_baseline=True)
        assert result == "烦", f"应判断为'烦'，实际={result}"

    def test_only_shortening_high_is_annoyed(self):
        """仅回复变短（得分≥1.0）→ 轻度烦"""
        from plugins.deepseek.conversation_fatigue import _classify_fatigue_type

        signals = {"reply_slowdown": 0.0, "message_shortening": 1.5}
        result = _classify_fatigue_type(level=1, signals=signals, has_baseline=True)
        assert result == "烦", f"回复明显变短应判断为'烦'，实际={result}"

    def test_level_0_no_type(self):
        """疲劳等级 0 → 无类型"""
        from plugins.deepseek.conversation_fatigue import _classify_fatigue_type

        signals = {"reply_slowdown": 0.0, "message_shortening": 0.0}
        result = _classify_fatigue_type(level=0, signals=signals, has_baseline=True)
        assert result == ""


class TestCorrelationAdjustedScore:
    """加权相关系数测试（真人化 P2-2，审计 audit-3-1）"""

    def test_both_zero_returns_zero(self):
        """两个信号都为 0 → 0"""
        from plugins.deepseek.conversation_fatigue import compute_correlation_adjusted_score

        result = compute_correlation_adjusted_score(0.0, 0.0)
        assert result == 0.0

    def test_only_one_signal(self):
        """仅一个信号 → 返回该信号得分"""
        from plugins.deepseek.conversation_fatigue import compute_correlation_adjusted_score

        # 仅长度缩短
        result = compute_correlation_adjusted_score(2.0, 0.0)
        assert result == 2.0

        # 仅速度变慢
        result = compute_correlation_adjusted_score(0.0, 2.0)
        assert result == 2.0

    def test_both_signals_discounted(self):
        """两个信号同时出现 → 折扣叠加"""
        from plugins.deepseek.conversation_fatigue import compute_correlation_adjusted_score

        # max(2,2) + min(2,2) * (1-0.3) = 2 + 2*0.7 = 3.4
        # 而不是简单相加 2+2=4
        result = compute_correlation_adjusted_score(2.0, 2.0)
        assert 3.0 <= result <= 3.8, f"预期 ~3.4，实际={result}"
        assert result < 4.0, "相关折扣后应小于简单相加"

    def test_discount_less_than_simple_sum(self):
        """任何情况下折扣分 ≤ 简单相加"""
        from plugins.deepseek.conversation_fatigue import compute_correlation_adjusted_score

        for s in [0.5, 1.0, 1.5, 2.0, 2.5]:
            for l in [0.5, 1.0, 1.5, 2.0, 2.5]:
                result = compute_correlation_adjusted_score(s, l)
                assert result <= s + l, f"s={s}, l={l}: {result} <= {s+l}"


class TestBaselineComputation:
    """基线计算统计测试"""

    def test_compute_baseline_from_messages(self):
        """从消息列表计算统计量"""
        from plugins.deepseek.conversation_fatigue import compute_user_baseline_from_messages

        msgs = [
            {"role": "user", "content": "今天天气不错", "timestamp": 1000},
            {"role": "user", "content": "是啊确实", "timestamp": 1060},
            {"role": "user", "content": "还行吧", "timestamp": 1120},
            {"role": "user", "content": "嗯好", "timestamp": 1180},
        ]
        avg_len, avg_gap, _, _ = compute_user_baseline_from_messages(msgs)

        assert 3.0 <= avg_len <= 8.0, f"平均长度应合理，实际={avg_len}"
        assert avg_gap == 60.0, f"平均间隔应为 60s，实际={avg_gap}"

    def test_too_few_messages_returns_zero(self):
        """消息太少返回全零"""
        from plugins.deepseek.conversation_fatigue import compute_user_baseline_from_messages

        msgs = [{"role": "user", "content": "只有一条", "timestamp": 1}]
        avg_len, avg_gap, sticker_rate, question_rate = compute_user_baseline_from_messages(msgs)

        assert avg_len == 0.0
        assert avg_gap == 0.0
        assert sticker_rate == 0.0
        assert question_rate == 0.0


class TestClosingMessageWithFatigueType:
    """收尾消息含忙/烦适配（真人化 P2-2）"""

    def test_busy_closing_is_concise(self):
        """忙时收尾简短识趣"""
        from plugins.deepseek.conversation_fatigue import get_closing_message

        msg = get_closing_message(3, None, fatigue_type="忙")
        assert msg is not None
        assert "忙" in msg or "回头" in msg or "不耽误" in msg

    def test_annoyed_closing_is_gentle(self):
        """烦时收尾更温柔"""
        from plugins.deepseek.conversation_fatigue import get_closing_message

        msg = get_closing_message(3, None, fatigue_type="烦")
        assert msg is not None
        assert "不打扰" in msg or "不烦你" in msg or "静一静" in msg or "不吵你" in msg
