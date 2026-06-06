"""Prompt 模块测试 — 覆盖模块化拼装、回复长度估算。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.prompt import (
    _build_system_prompt, _build_state_hints, estimate_reply_length,
    _CORE_PERSONA, _STICKER_RULES, _SHARE_RULES, _LOCATION_RULES,
)


class TestModularPrompt:
    def _base_kwargs(self, **overrides):
        base = {
            "affection": {"score": 50, "level": 1},
            "mood": {"mood": "平淡", "score": 50},
            "length": {"target_lines": 2, "style": "自然闲聊"},
            "relevant_memories": None, "recent_shares": None,
            "user_msg": "你好", "context_analysis": None,
            "emotion_state": None, "search_context": "",
            "reminder_context": "", "world_context": "", "bot_mood": None,
        }
        base.update(overrides)
        return base

    def test_simple_greeting_no_sticker_rules(self):
        prompt = _build_system_prompt(**self._base_kwargs(user_msg="你好"))
        assert _CORE_PERSONA[:20] in prompt
        assert _STICKER_RULES[:20] not in prompt

    def test_long_msg_includes_sticker_rules(self):
        prompt = _build_system_prompt(**self._base_kwargs(user_msg="你觉得今天那个表情包怎么样"))
        assert _STICKER_RULES[:20] in prompt

    def test_share_context_includes_share_rules(self):
        prompt = _build_system_prompt(**self._base_kwargs(
            recent_shares=[{"type": "链接", "summary": "测试"}]
        ))
        assert _SHARE_RULES[:20] in prompt

    def test_no_share_no_share_rules(self):
        prompt = _build_system_prompt(**self._base_kwargs())
        assert _SHARE_RULES[:20] not in prompt

    def test_weather_context_includes_location_rules(self):
        prompt = _build_system_prompt(**self._base_kwargs(world_context="上海 多云 25°C"))
        assert _LOCATION_RULES[:20] in prompt

    def test_affection_hints(self):
        hints = _build_state_hints({"score": 600}, {"mood": "开心", "score": 80})
        assert any("亲密" in h for h in hints)

    def test_low_affection_no_hint(self):
        hints = _build_state_hints({"score": 10}, {"mood": "平淡", "score": 50})
        assert not any("亲密" in h or "好感" in h or "喜欢" in h for h in hints)


class TestReplyLengthEstimation:
    def test_short_message(self):
        result = estimate_reply_length("你好", [])
        assert result["target_lines"] <= 2

    def test_emotional_message(self):
        result = estimate_reply_length("我今天好难过啊，想哭", [])
        assert result["style"] == "共情回应，简短但走心"

    def test_question_message(self):
        result = estimate_reply_length("你觉得这个怎么样？", [])
        assert result["style"] == "简洁回答，不用展开太多"

    def test_bot_angry_mood(self):
        result = estimate_reply_length("你好", [], bot_mood={"dominant": "生气"})
        assert result["style"] == "冷淡不耐烦，回得短"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
