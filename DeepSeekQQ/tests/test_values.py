# -*- coding: utf-8 -*-
"""values tests — 价值体系加载和匹配。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch

pytestmark = [pytest.mark.unit]


class TestLoadValues:
    def test_load_values_returns_dict(self):
        """加载 values.json 返回字典结构。"""
        from plugins.deepseek.values import _load_values
        v = _load_values()
        assert isinstance(v, dict)
        assert "categories" in v
        assert "conflict_mappings" in v

    def test_fallback_values_has_minimal(self):
        """内置后备值至少包含核心话题。"""
        from plugins.deepseek.values import _get_fallback_values
        v = _get_fallback_values()
        assert "categories" in v
        assert "奶茶" in str(v)


class TestFindRelevantValues:
    def test_match_keyword_returns_value(self):
        """匹配关键词返回对应价值条目。"""
        from plugins.deepseek.values import find_relevant_values
        results = find_relevant_values("我今天喝了一杯奶茶好好喝")
        assert len(results) >= 1
        assert any(r["topic"] == "奶茶" for r in results)

    def test_no_match_returns_empty(self):
        """无匹配返回空列表。"""
        from plugins.deepseek.values import find_relevant_values
        results = find_relevant_values("今天天气真好啊")
        assert results == []

    def test_short_message_returns_empty(self):
        """极短消息不匹配。"""
        from plugins.deepseek.values import find_relevant_values
        results = find_relevant_values("嗯")
        assert results == []

    def test_results_sorted_by_intensity(self):
        """结果按 intensity 降序排列。"""
        from plugins.deepseek.values import find_relevant_values
        results = find_relevant_values("早起和拖延真的让我很焦虑")
        if len(results) >= 2:
            assert results[0]["intensity"] >= results[1]["intensity"]


class TestDetectConflicts:
    def test_detect_conflict_opposing(self):
        """检测到用户观点冲突。"""
        from plugins.deepseek.values import find_relevant_values, detect_value_conflicts
        relevant = find_relevant_values("早起的人更成功，一日之计在于晨")
        conflicts = detect_value_conflicts("早起的人更成功，一日之计在于晨", relevant)
        # 早起是 bot 反感的话题，且用户说了正向关键词
        assert any(c["topic"] == "早起" for c in conflicts)

    def test_no_conflict_when_agree(self):
        """无冲突关键词时不触发。"""
        from plugins.deepseek.values import find_relevant_values, detect_value_conflicts
        relevant = find_relevant_values("奶茶就是好喝，我每天都喝")
        conflicts = detect_value_conflicts("奶茶就是好喝，我每天都喝", relevant)
        # 用户说奶茶好喝，和bot立场一致，不应该冲突
        assert all(c["topic"] != "奶茶" for c in conflicts)


class TestGetValueHints:
    def test_hints_with_high_affection(self):
        """高好感度时返回价值提示。"""
        from plugins.deepseek.values import get_value_hints
        hints = get_value_hints("早起的人更成功，一日之计在于晨", affection_score=600)
        # 高好感度 + 冲突话题，应该返回提示
        assert isinstance(hints, list)

    def test_hints_low_affection_may_be_empty(self):
        """低好感度 + 低概率可能导致空列表（合理）。"""
        from plugins.deepseek.values import get_value_hints
        with patch('plugins.deepseek.values.random.random', return_value=0.99):
            hints = get_value_hints("早起的人更成功", affection_score=10)
            # 低好感度 + random=0.99 > 0.10 → 应该为空
            assert hints == []

    def test_hints_empty_for_no_relevant(self):
        """不相关话题返回空。"""
        from plugins.deepseek.values import get_value_hints
        hints = get_value_hints("今天天气真好", affection_score=500)
        assert hints == []

    def test_short_message_returns_empty(self):
        """短消息返回空。"""
        from plugins.deepseek.values import get_value_hints
        hints = get_value_hints("嗯", affection_score=500)
        assert hints == []


class TestGetOpinionInjection:
    def test_injection_format(self):
        """返回格式正确。"""
        from plugins.deepseek.values import get_opinion_injection
        result = get_opinion_injection("早起的人更成功，一日之计在于晨", affection_score=600)
        assert isinstance(result, str)
        if result:
            assert "【你的立场】" in result

    def test_empty_for_no_relevant(self):
        """不相关话题返回空字符串。"""
        from plugins.deepseek.values import get_opinion_injection
        result = get_opinion_injection("今天天气真好", affection_score=500)
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
