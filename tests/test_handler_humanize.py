# -*- coding: utf-8 -*-
"""handler_humanize 测试 — 拟人化功能。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.handler_humanize import introduce_stutter, introduce_typo, introduce_mind_change, introduce_uncertainty
pytestmark = [pytest.mark.unit]



class TestIntroduceTypo:
    def test_short_text_unchanged(self):
        assert introduce_typo("你好") == "你好"

    def test_inserts_typo_and_correction(self):
        text = "我觉得这个真的不是很好看"
        results = set()
        for _ in range(50):
            result = introduce_typo(text)
            results.add(result)
        assert any(r != text for r in results)

    def test_no_match_unchanged(self):
        text = "abcdefghij"
        assert introduce_typo(text) == text


class TestIntroduceMindChange:
    def test_short_text_unchanged(self):
        """10 字以下不变。"""
        assert introduce_mind_change("你好呀") == "你好呀"

    def test_adds_pivot(self):
        text = "今天天气真不错啊，出去走走吧"
        results = set()
        for _ in range(50):
            results.add(introduce_mind_change(text))
        assert any(r != text for r in results)

    def test_pivot_prefix_present(self):
        text = "今天天气真不错啊，出去走走吧"
        # 至少有一个结果以 pivot 开头
        results = [introduce_mind_change(text) for _ in range(50)]
        pivots = ["等等", "算了", "嗯让", "不对", "等下", "啊算"]
        has_pivot = any(any(r.startswith(p) for p in pivots) for r in results)
        assert has_pivot


class TestIntroduceUncertainty:
    def test_always_prepends(self):
        """introduce_uncertainty 总是添加前缀。"""
        text = "这个答案应该是正确的"
        result = introduce_uncertainty(text)
        assert result != text
        assert len(result) > len(text)

    def test_prefix_present(self):
        text = "这个答案应该是正确的"
        prefixes = ["不太确定", "好像", "我记得", "印象中", "感觉"]
        results = [introduce_uncertainty(text) for _ in range(50)]
        has_prefix = any(any(r.startswith(p) for p in prefixes) for r in results)
        assert has_prefix


class TestIntroduceStutter:
    def test_short_text_unchanged(self):
        """4字以下不变。"""
        assert introduce_stutter("你好") == "你好"
        assert introduce_stutter("hi") == "hi"

    def test_stutter_modifies_text(self):
        """基本口吃效果：多次调用应该产生变化。"""
        text = "我觉得这个还不错的样子"
        results = set()
        for _ in range(100):
            results.add(introduce_stutter(text))
        # 至少有一些结果不同于原文
        assert any(r != text for r in results)

    def test_stutter_starter_repeat(self):
        """句首可重复的单字会产生重复效果。"""
        text = "我觉得这个不太对"
        results = set()
        for _ in range(200):
            r = introduce_stutter(text, arousal=0.5)
            results.add(r)
        # 应该有"我我我"开头的变体
        has_stutter = any(r.startswith("我我") for r in results)
        assert has_stutter, f"Expected some results with leading stutter, got: {list(results)[:5]}"

    def test_stutter_interjection_repeat(self):
        """语气词会产生重复。"""
        text = "嗯你说的对"
        results = set()
        for _ in range(200):
            r = introduce_stutter(text, arousal=0.5)
            results.add(r)
        has_stutter = any("嗯嗯嗯" in r for r in results)
        assert has_stutter, f"Expected some results with interjection stutter, got: {list(results)[:5]}"

    def test_stutter_negation_repeat(self):
        """否定词会产生重复。"""
        text = "不是这样的我没有"
        results = set()
        for _ in range(200):
            r = introduce_stutter(text, arousal=0.5)
            results.add(r)
        has_stutter = any("不不不" in r for r in results)
        assert has_stutter, f"Expected some results with negation stutter, got: {list(results)[:5]}"

    def test_stutter_with_high_arousal(self):
        """高arousal不影响函数本身（概率由调用方控制）。"""
        text = "我真的很生气"
        # 函数不应该因为 arousal 参数而抛异常
        result = introduce_stutter(text, arousal=0.9)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_stutter_no_cq_code_damage(self):
        """不包含CQ码时正常处理。"""
        text = "我觉得这个还挺好的呢"
        result = introduce_stutter(text)
        assert isinstance(result, str)
        assert len(result) >= len(text) - 1  # 口吃可能略微改变长度


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
