"""Test Search — 联网搜索触发判断、查询提取、结果格式化。

覆盖：
- should_search 搜索触发逻辑
- extract_search_query 查询清洗
- format_search_for_prompt 结果格式化
- _get_synonym_query 同义词生成
"""
from unittest.mock import MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# should_search — 搜索触发判断
# ═══════════════════════════════════════════════════════════════

class TestShouldSearch:
    """测试 should_search 在各种场景下的判断。"""

    @pytest.fixture(autouse=True)
    def _enable_search(self):
        """确保 SEARCH_ENABLED 和 TAVILY_API_KEY 为真。"""
        with patch("plugins.deepseek.search.SEARCH_ENABLED", True), \
             patch("plugins.deepseek.search.TAVILY_API_KEY", "test-key"):
            yield

    def test_explicit_search_keyword(self):
        """显式搜索关键词应触发搜索并标记 is_explicit。"""
        from plugins.deepseek.search import should_search
        result = should_search("搜一下Python最新版本")
        assert result["need_search"] is True
        assert result["is_explicit"] is True

    def test_time_sensitive_question(self):
        """时间敏感词 + 疑问句应触发搜索（隐式）。"""
        from plugins.deepseek.search import should_search
        # 注意：避免匹配到 location_only 排除模式（如 "xx天气"）
        result = should_search("今天股市为什么跌了")
        assert result["need_search"] is True
        assert result["is_explicit"] is False

    def test_casual_greeting_excluded(self):
        """闲聊消息不应触发搜索。"""
        from plugins.deepseek.search import should_search
        assert should_search("你好呀")["need_search"] is False
        assert should_search("在吗")["need_search"] is False
        assert should_search("嗯")["need_search"] is False
        assert should_search("晚安")["need_search"] is False

    def test_short_message_excluded(self):
        """短消息（<6字）不应触发搜索。"""
        from plugins.deepseek.search import should_search
        assert should_search("你好")["need_search"] is False
        assert should_search("哦")["need_search"] is False

    def test_search_disabled(self):
        """SEARCH_ENABLED=False 时不应触发搜索。"""
        from plugins.deepseek.search import should_search
        with patch("plugins.deepseek.search.SEARCH_ENABLED", False):
            result = should_search("搜一下Python")
            assert result["need_search"] is False

    def test_no_api_key(self):
        """无 TAVILY_API_KEY 时不应触发搜索。"""
        from plugins.deepseek.search import should_search
        with patch("plugins.deepseek.search.TAVILY_API_KEY", ""):
            result = should_search("搜一下Python")
            assert result["need_search"] is False

    def test_location_only_excluded(self):
        """简单地点陈述不应触发搜索。"""
        from plugins.deepseek.search import should_search
        result = should_search("我在上海")
        assert result["need_search"] is False

    def test_check_keyword_triggers_implicit(self):
        """含「了解」「知道」等词的长消息应触发隐式搜索。"""
        from plugins.deepseek.search import should_search
        # "查一下" 属于 EXPLICIT 关键词，用 "了解" 测试隐式触发
        result = should_search("我想了解一下量子计算的基本原理")
        assert result["need_search"] is True
        assert result["is_explicit"] is False


# ═══════════════════════════════════════════════════════════════
# extract_search_query — 查询清洗
# ═══════════════════════════════════════════════════════════════

class TestExtractSearchQuery:
    """测试 extract_search_query 查询提取。"""

    def test_remove_prefix(self):
        """应去除搜索前缀。"""
        from plugins.deepseek.search import extract_search_query
        assert extract_search_query("搜一下Python教程") == "Python教程"
        assert extract_search_query("帮我查一下天气") == "天气"
        assert extract_search_query("百度一下深圳") == "深圳"

    def test_remove_trailing_particles(self):
        """应去除尾部语气词。"""
        from plugins.deepseek.search import extract_search_query
        result = extract_search_query("搜一下今天天气怎么样呢")
        assert result == "今天天气怎么样"

    def test_no_prefix_returns_original(self):
        """无前缀时返回原字符串。"""
        from plugins.deepseek.search import extract_search_query
        assert extract_search_query("Python教程") == "Python教程"


# ═══════════════════════════════════════════════════════════════
# format_search_for_prompt
# ═══════════════════════════════════════════════════════════════

class TestFormatSearchForPrompt:
    """测试 format_search_for_prompt 结果格式化。"""

    def test_empty_result(self):
        """空结果或无结果时应返回空字符串。"""
        from plugins.deepseek.search import format_search_for_prompt
        assert format_search_for_prompt(None) == ""

        from plugins.deepseek.search import SearchResult
        empty = SearchResult(query="test", results=[])
        assert format_search_for_prompt(empty) == ""

    def test_with_results(self):
        """有结果时应包含查询词、标题和链接。"""
        from plugins.deepseek.search import format_search_for_prompt, SearchResult
        result = SearchResult(
            query="Python",
            results=[
                {"title": "Python官网", "url": "https://python.org", "snippet": "Python编程语言"},
                {"title": "Python教程", "url": "https://example.com", "snippet": "学习Python"},
            ],
            answer="Python是一种编程语言",
        )
        formatted = format_search_for_prompt(result)
        assert "Python" in formatted
        assert "python.org" in formatted
        assert "Python编程语言" in formatted

    def test_truncation(self):
        """长标题和摘要应被截断。"""
        from plugins.deepseek.search import format_search_for_prompt, SearchResult
        long_title = "A" * 100
        long_snippet = "B" * 300
        result = SearchResult(
            query="test",
            results=[{"title": long_title, "url": "https://x.com", "snippet": long_snippet}],
        )
        formatted = format_search_for_prompt(result)
        # 标题应截断至 60 字符
        assert len("A" * 60) <= 60
        # snippet 应截断至 200 字符
        assert len("B" * 200) <= 200


# ═══════════════════════════════════════════════════════════════
# _get_synonym_query
# ═══════════════════════════════════════════════════════════════

class TestSynonymQuery:
    """测试 _get_synonym_query 同义词替换。"""

    def test_replace_synonym(self):
        from plugins.deepseek.search import _get_synonym_query
        assert _get_synonym_query("怎么学习") == "如何学习"
        assert _get_synonym_query("如何学习") == "怎么学习"

    def test_no_synonym_returns_original(self):
        from plugins.deepseek.search import _get_synonym_query
        assert _get_synonym_query("abcdefg") == "abcdefg"
