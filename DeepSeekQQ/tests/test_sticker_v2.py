"""sticker 模块测试 — 标签解析、过滤。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.sticker import parse_sticker_tag, filter_sticker_tag
pytestmark = [pytest.mark.unit]



class TestParseStickerTag:
    def test_emotion_and_scene(self):
        text = "好的喵~[sticker:happy|撒娇]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert clean == "好的喵~"
        assert emotion == "happy"
        assert scene == "撒娇"

    def test_emotion_only(self):
        text = "哼[sticker:tsundere]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert clean == "哼"
        assert emotion == "tsundere"
        assert scene == ""

    def test_no_tag(self):
        text = "今天天气不错"
        clean, emotion, scene = parse_sticker_tag(text)
        assert clean == text
        assert emotion is None
        assert scene == ""

    def test_chinese_emotion_mapped(self):
        text = "哈哈[sticker:开心]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert emotion == "happy"

    def test_default_tag(self):
        text = "嗯[sticker]"
        clean, emotion, scene = parse_sticker_tag(text)
        assert emotion == "default"


class TestFilterStickerTag:
    def test_no_tag_passthrough(self):
        text, kept = filter_sticker_tag("今天天气不错", "test_session")
        assert text == "今天天气不错"
        assert not kept

    def test_probability_keeps_tag(self):
        """keep_probability=1.0 应该总是保留。"""
        text, kept = filter_sticker_tag(
            "好的[sticker:happy]", "test_session", keep_probability=1.0
        )
        assert kept
        assert "[sticker:happy]" in text

    def test_probability_removes_tag(self):
        """keep_probability=0.0 应该总是移除。"""
        text, kept = filter_sticker_tag(
            "好的[sticker:happy]", "test_session", keep_probability=0.0
        )
        assert not kept
        assert "[sticker:happy]" not in text
        assert "好的" == text.strip()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
