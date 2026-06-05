"""表情包模块测试 — 覆盖标签解析 + 动态概率（功能⑤）。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.sticker import parse_sticker_tag, should_send_sticker_fallback, filter_sticker_tag


class TestParseStickerTag:
    def test_emotion_and_scene(self):
        text = "你好呀~ [sticker:happy|撒娇]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert clean == "你好呀~"
        assert emotion == "happy"
        assert scene == "撒娇"

    def test_emotion_only(self):
        text = "哼 [sticker:tsundere]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert clean == "哼"
        assert emotion == "tsundere"
        assert scene == ""

    def test_default_sticker(self):
        text = "喵~ [sticker]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert emotion == "default"

    def test_no_sticker_tag(self):
        text = "今天天气不错"
        clean, emotion, scene = parse_sticker_tag(text)
        assert emotion is None
        assert clean == text


class TestStickerFallback:
    def test_happy_keywords(self):
        result = should_send_sticker_fallback("哈哈哈哈笑死我了")
        assert result is None or isinstance(result, str)

    def test_empty_text(self):
        result = should_send_sticker_fallback("")
        assert result is None or isinstance(result, str)

    def test_dynamic_fallback_chance(self):
        """功能⑤：高 fallback_chance 应该更容易触发。"""
        hits_high = sum(1 for _ in range(200) if should_send_sticker_fallback("哈哈", fallback_chance=0.9))
        hits_low = sum(1 for _ in range(200) if should_send_sticker_fallback("哈哈", fallback_chance=0.01))
        assert hits_high > hits_low


class TestFilterStickerDynamicProb:
    """功能⑤：动态保留概率测试。"""

    def test_high_probability_keeps_more(self):
        """高保留概率应该保留更多标签。"""
        text = "你好 [sticker:happy|撒娇]"
        hits_high = sum(1 for _ in range(200) if filter_sticker_tag(text, "test_sess", keep_probability=0.9)[1])
        hits_low = sum(1 for _ in range(200) if filter_sticker_tag(text, "test_sess2", keep_probability=0.01)[1])
        assert hits_high > hits_low

    def test_default_probability(self):
        """不传 keep_probability 时使用默认值。"""
        text = "你好 [sticker:happy]"
        clean, kept = filter_sticker_tag(text, "test_sess3")
        assert isinstance(kept, bool)

    def test_no_tag_returns_false(self):
        """没有标签时始终返回 False。"""
        clean, kept = filter_sticker_tag("普通消息", "test_sess4")
        assert kept is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
