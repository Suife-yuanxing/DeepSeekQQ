# -*- coding: utf-8 -*-
"""schedule 测试 — 作息状态机与每日随机偏移量。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
import plugins.deepseek.schedule as sched

pytestmark = [pytest.mark.unit]


def _reset_schedule_cache():
    """重置 schedule 模块的偏移量缓存（在测试间使用）。"""
    sched._offset_date = ""
    sched._daily_offset_cache = {}


class TestScheduleBasics:
    """基本时段判断（不涉及偏移量）。"""

    def test_noon_is_meal(self):
        state = sched.get_schedule_state(hour=12, weekday=3)
        assert state.period == "meal"

    def test_midnight_is_night_owl(self):
        state = sched.get_schedule_state(hour=0, weekday=3)
        assert state.period == "night_owl"

    def test_early_morning_is_sleeping(self):
        state = sched.get_schedule_state(hour=4, weekday=3)
        assert state.period == "sleeping"

    def test_evening_is_active(self):
        state = sched.get_schedule_state(hour=20, weekday=3)
        assert state.period == "active"
        assert state.verbosity == "chatty"

    def test_weekend_sleeps_later(self):
        """周末8点仍在睡觉（工作日已起床）。"""
        state_weekday = sched.get_schedule_state(hour=8, weekday=3)
        state_weekend = sched.get_schedule_state(hour=8, weekday=5)
        # 工作日8点已起床（waking或active）
        assert state_weekday.period in ("waking", "active")
        # 周末8点可能还在睡觉（取决于偏移量），不应该是active
        assert state_weekend.period != "active"

    def test_schedule_returns_valid_state(self):
        """所有24小时都应返回有效的ScheduleState。"""
        valid_periods = {"sleeping", "waking", "active", "meal", "lazy", "night_owl"}
        for h in range(24):
            state = sched.get_schedule_state(hour=h, weekday=3)
            assert state.period in valid_periods, f"Hour {h} returned {state.period}"
            assert 0.0 <= state.energy <= 1.0
            assert state.verbosity in ("minimal", "normal", "chatty")


class TestDailyOffsets:
    """每日偏移量测试。"""

    def test_offset_consistency_same_day(self):
        """同一天多次调用偏移量一致。"""
        _reset_schedule_cache()

        with patch('plugins.deepseek.schedule.datetime') as mock_dt:
            today_str = "2026-06-12"
            mock_now = MagicMock()
            mock_now.strftime.return_value = today_str
            mock_now.hour = 10
            mock_now.minute = 30
            mock_now.weekday.return_value = 3
            mock_dt.now.return_value = mock_now

            sched._ensure_daily_offsets()
            first = dict(sched._daily_offset_cache)

            sched._ensure_daily_offsets()
            second = dict(sched._daily_offset_cache)

            assert first == second, "同一日偏移量应该相同"
            assert len(first) == 6, f"Expected 6 offset keys, got {len(first)}"

    def test_offset_refresh_new_day(self):
        """跨天偏移量刷新。"""
        _reset_schedule_cache()

        with patch('plugins.deepseek.schedule.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-06-12"
            mock_now.hour = 10
            mock_now.minute = 30
            mock_now.weekday.return_value = 3
            mock_dt.now.return_value = mock_now

            sched._ensure_daily_offsets()
            assert sched._offset_date == "2026-06-12"

        # 新的一天会刷新
        with patch('plugins.deepseek.schedule.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-06-13"
            mock_now.hour = 10
            mock_now.minute = 30
            mock_now.weekday.return_value = 3
            mock_dt.now.return_value = mock_now

            sched._ensure_daily_offsets()
            assert sched._offset_date == "2026-06-13"

    def test_offset_keys_and_ranges(self):
        """偏移量字典包含所有必要字段且值在合法范围。"""
        _reset_schedule_cache()

        with patch('plugins.deepseek.schedule.datetime') as mock_dt:
            mock_now = MagicMock()
            mock_now.strftime.return_value = "2026-06-12"
            mock_now.hour = 10
            mock_now.minute = 30
            mock_now.weekday.return_value = 3
            mock_dt.now.return_value = mock_now

            sched._ensure_daily_offsets()

        required_keys = {"sleep", "wake", "weekend_late", "weekend_late_minutes", "skip_class", "late_night"}
        cache = sched._daily_offset_cache
        assert required_keys <= set(cache.keys()), f"Missing keys: {required_keys - set(cache.keys())}"

        assert -45 <= cache["sleep"] <= 45
        assert -30 <= cache["wake"] <= 30
        assert isinstance(cache["weekend_late"], bool)
        assert isinstance(cache["skip_class"], bool)
        assert isinstance(cache["late_night"], bool)


class TestSkipClass:
    """逃课状态测试。"""

    def test_skip_class_on_weekday(self):
        """工作日且 skip_class=True 时触发逃课。"""
        from datetime import datetime as real_dt
        _reset_schedule_cache()

        # 用今天的日期避免 _ensure_daily_offsets 刷新
        today = real_dt.now().strftime("%Y-%m-%d")
        sched._offset_date = today
        sched._daily_offset_cache = {
            "sleep": 0, "wake": 0,
            "weekend_late": False, "weekend_late_minutes": 0,
            "skip_class": True,
            "late_night": False,
        }

        state = sched.get_schedule_state(hour=10, weekday=3)
        assert state.period == "lazy", f"Expected lazy (skip_class), got {state.period}"

    def test_skip_class_not_on_weekend(self):
        """周末不触发逃课。"""
        from datetime import datetime as real_dt
        _reset_schedule_cache()

        today = real_dt.now().strftime("%Y-%m-%d")
        sched._offset_date = today
        sched._daily_offset_cache = {
            "sleep": 0, "wake": 0,
            "weekend_late": False, "weekend_late_minutes": 0,
            "skip_class": True,  # 即使True，周末也不触发
            "late_night": False,
        }

        state = sched.get_schedule_state(hour=10, weekday=5)
        # 周末10点不会逃课
        assert "逃课" not in state.description
        assert "不想上课" not in state.description


class TestLateNight:
    """深夜不睡状态测试。"""

    def test_late_night_overrides_sleep(self):
        """late_night=True时，凌晨1点不睡觉而是night_owl。"""
        from datetime import datetime as real_dt
        _reset_schedule_cache()

        today = real_dt.now().strftime("%Y-%m-%d")
        sched._offset_date = today
        sched._daily_offset_cache = {
            "sleep": 0, "wake": 0,
            "weekend_late": False, "weekend_late_minutes": 0,
            "skip_class": False,
            "late_night": True,
        }

        state = sched.get_schedule_state(hour=1, weekday=3)
        assert state.period == "night_owl", f"Expected night_owl, got {state.period}"
        assert "不困" in state.description or "追番" in state.description

    def test_normal_sleep_when_no_late_night(self):
        """late_night=False时，凌晨1点正常睡觉。"""
        from datetime import datetime as real_dt
        _reset_schedule_cache()

        today = real_dt.now().strftime("%Y-%m-%d")
        sched._offset_date = today
        sched._daily_offset_cache = {
            "sleep": 0, "wake": 0,
            "weekend_late": False, "weekend_late_minutes": 0,
            "skip_class": False,
            "late_night": False,
        }

        state = sched.get_schedule_state(hour=1, weekday=3)
        assert state.period == "sleeping", f"Expected sleeping, got {state.period}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
