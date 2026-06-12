# -*- coding: utf-8 -*-
"""promise_tracker 测试 — 承诺提取、到期估算、遗忘机制。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import time
from plugins.deepseek.promise_tracker import (
    extract_promises, should_forget, estimate_due_time,
    get_forgotten_apology, get_fulfill_prefix,
)

pytestmark = [pytest.mark.unit]


class TestExtractPromises:
    """承诺提取测试。"""

    def test_extract_mingtian(self):
        """提取'明天'承诺。"""
        results = extract_promises("那我明天告诉你吧", "user123", "sess456")
        assert len(results) >= 1
        assert results[0]["due_hint"] == "明天"
        assert results[0]["user_id"] == "user123"

    def test_extract_xiaci(self):
        """提取'下次'承诺。"""
        results = extract_promises("下次再跟你说哦", "user123", "sess456")
        assert len(results) >= 1
        assert any(r["due_hint"] == "下次" for r in results)

    def test_extract_huitou(self):
        """提取'回头'承诺。"""
        results = extract_promises("回头帮你查一下", "user123", "sess456")
        assert len(results) >= 1
        assert any(r["due_hint"] == "回头" for r in results)

    def test_extract_dengxia(self):
        """提取'等下'承诺。"""
        results = extract_promises("等下我看看哈", "user123", "sess456")
        assert len(results) >= 1

    def test_extract_wandian(self):
        """提取'晚点'承诺。"""
        results = extract_promises("晚点给你发", "user123", "sess456")
        assert len(results) >= 1

    def test_exclude_greeting(self):
        """排除非承诺模式（明天见）。"""
        results = extract_promises("明天见啦", "user123", "sess456")
        # "明天见" 应该被排除
        assert len(results) == 0

    def test_exclude_dont_know(self):
        """排除不确定性表达。"""
        results = extract_promises("明天还不知道呢", "user123", "sess456")
        assert len(results) == 0

    def test_empty_text(self):
        """空文本不产生承诺。"""
        results = extract_promises("", "user123", "sess456")
        assert len(results) == 0

    def test_no_promise_pattern(self):
        """没有承诺模式的文本。"""
        results = extract_promises("今天天气真好啊", "user123", "sess456")
        assert len(results) == 0

    def test_short_match_skipped(self):
        """太短的匹配不算承诺。"""
        results = extract_promises("明天吧", "user123", "sess456")
        # "明天吧" 太短（<4字），应跳过
        assert len(results) == 0

    def test_multiple_promises(self):
        """一则消息中多个承诺。"""
        results = extract_promises(
            "明天帮你查一下，下次再详细说", "user123", "sess456"
        )
        assert len(results) >= 1  # 至少提取一个


class TestEstimateDueTime:
    """到期时间估算测试。"""

    def test_mingtian_due(self):
        now = time.time()
        due = estimate_due_time("明天", now)
        # 应该在24-28小时后
        assert 86400 <= due - now <= 100800  # 86400+14400

    def test_dengxia_due(self):
        now = time.time()
        due = estimate_due_time("等下", now)
        # 应该在0.5-2小时后
        assert 1800 <= due - now <= 7200

    def test_xiaci_due(self):
        now = time.time()
        due = estimate_due_time("下次", now)
        # 应该在1-3天后
        assert 86400 <= due - now <= 259200

    def test_unknown_hint_defaults_to_day(self):
        now = time.time()
        due = estimate_due_time("未知", now)
        assert due > now


class TestShouldForget:
    """故意遗忘测试。"""

    def test_distribution_reasonable(self):
        """20%概率遗忘，200次中应有大约40次。"""
        results = [should_forget() for _ in range(500)]
        forget_count = sum(results)
        # 允许较大误差（15-25%）
        assert 50 <= forget_count <= 150, f"Expected ~100, got {forget_count}"


class TestTemplates:
    """模板生成测试。"""

    def test_apology_not_empty(self):
        msg = get_forgotten_apology("帮你查资料")
        assert len(msg) > 5
        assert "帮你查资料" in msg or "..." in msg  # 可能被format替换

    def test_fulfill_prefix_not_empty(self):
        msg = get_fulfill_prefix("告诉你答案")
        assert len(msg) > 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
