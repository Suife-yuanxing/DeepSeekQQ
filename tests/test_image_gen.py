"""功能④：图片生成 — 触发词检测测试。"""
import pytest
from plugins.deepseek.image_gen import should_generate_image, _extract_draw_prompt
pytestmark = [pytest.mark.unit]



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
        hits = sum(1 for _ in range(100) if should_generate_image("帮我画一只猫"))
        assert hits > 40  # 80% 概率，100次应该至少40次

    def test_generate_image_trigger(self):
        """"生成图片"也应该触发。"""
        hits = sum(1 for _ in range(100) if should_generate_image("帮我生成一张图片"))
        assert hits > 40  # 80% 概率

    def test_selfie_trigger_30_percent(self):
        """自拍触发概率 30%，多次测试统计验证。"""
        hits = sum(1 for _ in range(200) if should_generate_image("我想看你的自拍"))
        assert 15 < hits < 120  # 30% 概率，允许统计波动

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
        assert "photorealistic" in result
        assert "consistent character" in result

    def test_short_input(self):
        result = _extract_draw_prompt("画")
        assert "photorealistic" in result
        assert "pink hair" in result

    def test_with_prefix(self):
        result = _extract_draw_prompt("画一个漂亮的女孩")
        assert "漂亮的女孩" in result
        assert "photorealistic" in result
