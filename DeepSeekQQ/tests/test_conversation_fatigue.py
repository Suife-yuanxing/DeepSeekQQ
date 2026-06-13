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
