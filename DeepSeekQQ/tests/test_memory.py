# -*- coding: utf-8 -*-
"""记忆系统测试 — 覆盖置信度、冷却、缓存清理。"""
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from plugins.deepseek.memory import (
    _recently_used_memories, _cleanup_memory_cache, _is_memory_relevant,
    _MEMORY_CACHE_MAX_USERS, MEMORY_COOLDOWN_ROUNDS, MAX_MEMORY_PER_REPLY,
)

pytestmark = [pytest.mark.unit]


def _make_cache_entry(tags=None):
    """B16: 创建新格式的缓存条目 (tags_list, timestamp)。"""
    return (tags or ["记忆"], time.time())


class TestMemoryRelevance:
    def test_relevant_by_keyword(self):
        # "喜欢" (2-char keyword) appears as substring in "喜欢喝咖啡"
        assert _is_memory_relevant("喜欢喝咖啡", "喜欢")

    def test_relevant_reverse(self):
        # Both contain "猫咪" (2-char keyword)
        assert _is_memory_relevant("用户养了一只猫咪", "我的猫咪很可爱")

    def test_irrelevant(self):
        assert not _is_memory_relevant("用户喜欢喝咖啡", "今天天气真不错")

    def test_empty_user_msg(self):
        assert not _is_memory_relevant("用户喜欢喝咖啡", "")

    def test_short_keywords_ignored(self):
        assert not _is_memory_relevant("用户A和B去C", "你好")


class TestMemoryCacheCleanup:
    def setup_method(self):
        _recently_used_memories.clear()

    def test_no_cleanup_under_limit(self):
        for i in range(50):
            _recently_used_memories[f"user_{i}"] = _make_cache_entry()
        _cleanup_memory_cache()
        assert len(_recently_used_memories) == 50

    def test_cleanup_over_limit(self):
        for i in range(_MEMORY_CACHE_MAX_USERS + 50):
            _recently_used_memories[f"user_{i}"] = _make_cache_entry()
        _cleanup_memory_cache()
        assert len(_recently_used_memories) <= _MEMORY_CACHE_MAX_USERS

    def test_cleanup_preserves_half(self):
        total = _MEMORY_CACHE_MAX_USERS + 100
        for i in range(total):
            _recently_used_memories[f"user_{i}"] = _make_cache_entry()
        _cleanup_memory_cache()
        assert len(_recently_used_memories) == _MEMORY_CACHE_MAX_USERS


class TestMemoryConstants:
    def test_cooldown_rounds(self):
        assert MEMORY_COOLDOWN_ROUNDS >= 1

    def test_max_memory_per_reply(self):
        assert MAX_MEMORY_PER_REPLY >= 1

    def test_cache_max_users(self):
        assert _MEMORY_CACHE_MAX_USERS >= 50


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
