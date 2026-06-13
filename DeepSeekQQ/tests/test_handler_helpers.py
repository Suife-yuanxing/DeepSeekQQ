# -*- coding: utf-8 -*-
"""handler_helpers 测试 — 引用决策、问候检测、消息分析。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.handler_helpers import (
    is_multi_topic, is_question, is_greeting, detect_greeting_type,
    get_morning_time_hint, get_night_affection_hint, has_time_gap,
    parse_target_lines,
)

pytestmark = [pytest.mark.unit]


class TestIsMultiTopic:
    def test_single_sentence(self):
        assert not is_multi_topic("今天天气怎么样")

    def test_two_sentences(self):
        assert is_multi_topic("今天天气怎么样？明天去哪玩？")

    def test_newline_separated(self):
        assert is_multi_topic("第一个话题\n第二个话题内容")

    def test_short_segments_filtered(self):
        assert not is_multi_topic("嗯。好。")


class TestIsQuestion:
    def test_question_mark(self):
        assert is_question("你是谁？")

    def test_english_question_mark(self):
        assert is_question("hello?")

    def test_keyword_why(self):
        assert is_question("为什么这样")

    def test_keyword_can(self):
        assert is_question("能不能帮我")

    def test_not_question(self):
        assert not is_question("今天天气不错")


class TestIsGreeting:
    def test_simple_greeting(self):
        assert is_greeting("嗯")
        assert is_greeting("好的")
        assert is_greeting("ok")
        assert is_greeting("收到")

    def test_not_greeting(self):
        assert not is_greeting("今天天气怎么样")
        assert not is_greeting("嗯我觉得这个不太对")


class TestDetectGreetingType:
    def test_morning(self):
        assert detect_greeting_type("早安") == "morning"
        assert detect_greeting_type("早上好呀") == "morning"

    def test_night(self):
        assert detect_greeting_type("晚安") == "night"
        assert detect_greeting_type("睡了") == "night"

    def test_none(self):
        assert detect_greeting_type("今天吃什么") is None


class TestGetMorningTimeHint:
    def test_early_morning(self):
        hint = get_morning_time_hint(6)
        assert len(hint) > 0

    def test_normal_morning(self):
        hint = get_morning_time_hint(8)
        assert len(hint) > 0

    def test_late_morning(self):
        hint = get_morning_time_hint(9)
        assert len(hint) > 0

    def test_outside_range(self):
        assert get_morning_time_hint(15) == ""


class TestGetNightAffectionHint:
    def test_high_affection(self):
        hint = get_night_affection_hint({"score": 300})
        assert len(hint) > 0

    def test_medium_affection(self):
        hint = get_night_affection_hint({"score": 80})
        assert len(hint) > 0

    def test_low_affection(self):
        hint = get_night_affection_hint({"score": 10})
        assert len(hint) > 0

    def test_none_affection(self):
        hint = get_night_affection_hint(None)
        assert len(hint) > 0


class TestHasTimeGap:
    def test_no_memories(self):
        assert not has_time_gap([])

    def test_recent_message(self):
        import time
        memories = [{"timestamp": time.time() - 60}]
        assert not has_time_gap(memories)

    def test_old_message(self):
        import time
        memories = [{"timestamp": time.time() - 600}]
        assert has_time_gap(memories)


class TestParseTargetLines:
    def test_single_number(self):
        assert parse_target_lines("3") == 3

    def test_range(self):
        for _ in range(20):
            result = parse_target_lines("2-4")
            assert 2 <= result <= 4

    def test_invalid_returns_default(self):
        assert parse_target_lines("abc") == 3

    def test_empty_returns_default(self):
        assert parse_target_lines("") == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
