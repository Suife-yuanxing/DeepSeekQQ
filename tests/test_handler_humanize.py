# -*- coding: utf-8 -*-
"""handler_humanize 测试 — 拟人化功能。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.handler_humanize import introduce_typo, introduce_mind_change, introduce_uncertainty


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
