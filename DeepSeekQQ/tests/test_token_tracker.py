"""Test Token Tracker — API 调用记录与成本统计。

覆盖：
- CallRecord dataclass 计算属性
- TokenTracker 记录/统计/持久化
- get_tracker 全局单例
- DailyStats 数据结构
"""
import json
import os
import tempfile
import time
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════
# CallRecord
# ═══════════════════════════════════════════════════════════════

class TestCallRecord:
    """测试 CallRecord dataclass 及计算属性。"""

    def test_input_tokens_estimation(self):
        """input_tokens 应基于 CHARS_PER_TOKEN 估算。"""
        from plugins.deepseek.token_tracker import CallRecord
        record = CallRecord(
            task_type="chat",
            model="deepseek-chat",
            input_chars=1500,
            output_chars=300,
            timestamp=time.time(),
        )
        expected = int(1500 / 1.5)
        assert record.input_tokens == expected

    def test_output_tokens_estimation(self):
        """output_tokens 同样基于字符/系数。"""
        from plugins.deepseek.token_tracker import CallRecord
        record = CallRecord(
            task_type="chat",
            model="deepseek-chat",
            input_chars=0,
            output_chars=750,
            timestamp=time.time(),
        )
        expected = int(750 / 1.5)
        assert record.output_tokens == expected

    def test_min_one_token(self):
        """即使输入字符很少，也至少返回 1 token。"""
        from plugins.deepseek.token_tracker import CallRecord
        record = CallRecord(
            task_type="chat",
            model="deepseek-chat",
            input_chars=0,
            output_chars=0,
            timestamp=time.time(),
        )
        assert record.input_tokens >= 1
        assert record.output_tokens >= 1

    def test_cost_calculation(self):
        """cost_usd 应基于定价表正确计算。"""
        from plugins.deepseek.token_tracker import CallRecord
        record = CallRecord(
            task_type="chat",
            model="deepseek-chat",
            input_chars=1500000,  # ~1M tokens
            output_chars=750000,   # ~0.5M tokens
            timestamp=time.time(),
        )
        # deepseek-chat: input $0.14/M, output $0.28/M
        expected_input_cost = record.input_tokens * 0.14 / 1e6
        expected_output_cost = record.output_tokens * 0.28 / 1e6
        assert abs(record.cost_usd - (expected_input_cost + expected_output_cost)) < 1e-9

    def test_cached_record(self):
        """cached=True 的记录应有该标记。"""
        from plugins.deepseek.token_tracker import CallRecord
        record = CallRecord(
            task_type="chat",
            model="deepseek-chat",
            input_chars=1000,
            output_chars=500,
            timestamp=time.time(),
            cached=True,
        )
        assert record.cached is True


# ═══════════════════════════════════════════════════════════════
# DailyStats
# ═══════════════════════════════════════════════════════════════

class TestDailyStats:
    """测试 DailyStats dataclass。"""

    def test_default_values(self):
        """默认值应全为 0 / 空。"""
        from plugins.deepseek.token_tracker import DailyStats
        stats = DailyStats(date="2026-06-18")
        assert stats.date == "2026-06-18"
        assert stats.calls == 0
        assert stats.input_tokens == 0
        assert stats.output_tokens == 0
        assert stats.cost_usd == 0.0
        assert stats.by_task == {}


# ═══════════════════════════════════════════════════════════════
# TokenTracker
# ═══════════════════════════════════════════════════════════════

class TestTokenTracker:
    """测试 TokenTracker 记录和统计。"""

    @pytest.fixture
    def tracker(self):
        """创建干净的 TokenTracker（绕过持久化文件）。"""
        from plugins.deepseek.token_tracker import TokenTracker
        with patch.object(TokenTracker, "_load", return_value=None):
            t = TokenTracker()
            t._records = []
            t._daily = {}
            return t

    def test_record_adds_to_daily(self, tracker):
        """record() 应更新 daily stats。"""
        tracker.record("chat", "deepseek-chat", input_chars=1500, output_chars=300)
        stats = tracker.get_stats()
        assert stats["today"]["calls"] == 1
        assert stats["today"]["input_tokens"] > 0
        assert stats["today"]["output_tokens"] > 0

    def test_cached_record_not_counted_in_tokens(self, tracker):
        """cached=True 的调用不计算 token 但计入次数。"""
        tracker.record("chat", "deepseek-chat", input_chars=1500, output_chars=300, cached=True)
        stats = tracker.get_stats()
        # 调用计数仍包含
        assert stats["today"]["calls"] == 1
        # 但 tokens 不计（cached 不消耗 API）
        assert stats["today"]["input_tokens"] == 0
        assert stats["today"]["output_tokens"] == 0

    def test_multiple_records(self, tracker):
        """多次记录应正确累加。"""
        for _ in range(5):
            tracker.record("chat", "deepseek-chat", input_chars=1500, output_chars=300)
        stats = tracker.get_stats()
        assert stats["today"]["calls"] == 5

    def test_task_type_tracking(self, tracker):
        """不同 task_type 应分别统计。"""
        tracker.record("chat", "deepseek-chat", input_chars=1000, output_chars=500)
        tracker.record("extract", "deepseek-chat", input_chars=500, output_chars=200)
        tracker.record("search", "deepseek-chat", input_chars=300, output_chars=100)
        stats = tracker.get_stats()
        assert stats["by_task"]["chat"] == 1
        assert stats["by_task"]["extract"] == 1
        assert stats["by_task"]["search"] == 1

    def test_get_stats_structure(self, tracker):
        """get_stats() 返回应包含所有预期键。"""
        stats = tracker.get_stats()
        assert "today" in stats
        assert "month" in stats
        assert "total" in stats
        assert "by_task" in stats
        assert "cache_hit_rate" in stats
        assert "recent_calls" in stats

    def test_recent_calls_limit(self, tracker):
        """recent_calls 最多返回 20 条。"""
        for i in range(25):
            tracker.record("chat", "deepseek-chat", input_chars=100, output_chars=50)
        stats = tracker.get_stats()
        assert len(stats["recent_calls"]) <= 20

    def test_reset_daily(self, tracker):
        """reset_daily() 应清空今日统计。"""
        tracker.record("chat", "deepseek-chat", input_chars=1500, output_chars=300)
        tracker.reset_daily()
        stats = tracker.get_stats()
        assert stats["today"]["calls"] == 0

    def test_persist_and_load(self, tracker, tmp_path):
        """持久化后再加载应恢复数据。"""
        from plugins.deepseek.token_tracker import TokenTracker
        tracker.record("chat", "deepseek-chat", input_chars=1500, output_chars=300)

        # 用临时文件替代统计文件
        stats_file = tmp_path / "token_stats.json"
        with patch("plugins.deepseek.token_tracker._STATS_FILE", str(stats_file)):
            with patch("plugins.deepseek.token_tracker._STATS_DIR", str(tmp_path)):
                tracker.persist()
                assert stats_file.exists()

                # 新 tracker 加载
                tracker2 = TokenTracker()
                tracker2._records = []
                tracker2._daily = {}
                tracker2._load()
                assert len(tracker2._daily) > 0


# ═══════════════════════════════════════════════════════════════
# get_tracker 单例
# ═══════════════════════════════════════════════════════════════

class TestGetTracker:
    """测试 get_tracker 全局单例。"""

    def test_singleton(self):
        """多次调用应返回同一实例。"""
        from plugins.deepseek.token_tracker import get_tracker
        # 重置
        import plugins.deepseek.token_tracker as tt
        tt._tracker = None
        t1 = get_tracker()
        t2 = get_tracker()
        assert t1 is t2


# ═══════════════════════════════════════════════════════════════
# _date_str
# ═══════════════════════════════════════════════════════════════

class TestDateStr:
    """测试 _date_str 辅助函数。"""

    def test_format(self):
        """应返回 YYYY-MM-DD 格式。"""
        from plugins.deepseek.token_tracker import _date_str
        import datetime
        ts = datetime.datetime(2026, 6, 18, 12, 0, 0).timestamp()
        assert _date_str(ts) == "2026-06-18"
