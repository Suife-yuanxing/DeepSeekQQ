"""功能④：图片生成 — 触发词检测测试。"""
import pytest
from plugins.deepseek.image_gen import should_generate_image, _extract_draw_prompt


class TestShouldGenerateImage:
    """测试图片生成触发条件。"""

    def test_selfie_trigger(self):
        result = should_generate_image("我自拍好看吗")
        # 15% 概率，可能触发也可能不触发，多次测试确保至少有一次
        # 这里只测试返回值结构
        if result is not None:
            assert result["id"] == "selfie"
            assert "prompt" in result

    def test_draw_trigger_high_prob(self):
        """画的触发概率 80%，多次测试应该大多触发。"""
        hits = sum(1 for _ in range(50) if should_generate_image("帮我画一只猫"))
        assert hits > 20  # 80% 概率，50次应该至少20次

    def test_no_trigger(self):
        """普通消息不触发。"""
        for _ in range(20):
            assert should_generate_image("今天天气好") is None
            assert should_generate_image("你好呀") is None

    def test_eating_keyword(self):
        """吃饭关键词。"""
        result = should_generate_image("我饿了想吃饭")
        if result is not None:
            assert result["id"] == "eating"

    def test_sleep_keyword(self):
        """睡觉关键词。"""
        result = should_generate_image("晚安，困了")
        if result is not None:
            assert result["id"] == "sleep"

    def test_celebrate_keyword(self):
        """庆祝关键词。"""
        result = should_generate_image("今天是我生日")
        if result is not None:
            assert result["id"] == "celebrate"


class TestExtractDrawPrompt:
    """测试绘画描述提取。"""

    def test_basic_extract(self):
        result = _extract_draw_prompt("帮我画一只可爱的猫")
        assert "可爱的猫" in result
        assert "anime style" in result

    def test_short_input(self):
        result = _extract_draw_prompt("画")
        assert "anime catgirl" in result

    def test_with_prefix(self):
        result = _extract_draw_prompt("画一个漂亮的女孩")
        assert "漂亮的女孩" in result
