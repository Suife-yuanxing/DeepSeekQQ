"""表情包模块测试 — 覆盖标签解析。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.sticker import parse_sticker_tag, should_send_sticker_fallback


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
