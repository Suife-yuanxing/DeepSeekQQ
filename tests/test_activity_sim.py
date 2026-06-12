# -*- coding: utf-8 -*-
"""activity_sim 测试 — 当前活动状态模拟。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch, MagicMock
import plugins.deepseek.activity_sim as act_sim

pytestmark = [pytest.mark.unit]


def _reset_activity_cache():
    """重置活动缓存。"""
    act_sim._current_activity = None
    act_sim._current_slot = ""
    act_sim._activity_updated = 0


class TestActivityPools:
    """活动池结构验证。"""

    def test_all_slots_have_pools(self):
        """每个时段都有活动池。"""
        expected_slots = {"morning", "noon", "afternoon", "dinner", "evening", "night"}
        assert set(act_sim.ACTIVITY_POOLS.keys()) == expected_slots

    def test_all_activities_have_required_fields(self):
        """每个活动都有name/action/emoji。"""
        for slot, pool in act_sim.ACTIVITY_POOLS.items():
            for activity, weight in pool:
                assert activity.name, f"{slot}: missing name"
                assert activity.action, f"{slot}: missing action"
                assert activity.emoji, f"{slot}: missing emoji"
                assert 0 < weight <= 100, f"{slot}: invalid weight {weight}"

    def test_weights_are_reasonable(self):
        """每个池的权重之和合理（大致100）。"""
        for slot, pool in act_sim.ACTIVITY_POOLS.items():
            total = sum(w for _, w in pool)
            assert 80 <= total <= 120, f"{slot}: weights sum to {total}, expected ~100"


class TestHourSlotMapping:
    """时间段映射测试。"""

    def test_morning_slots(self):
        assert act_sim._HOUR_TO_SLOT.get(9) == "morning"
        assert act_sim._HOUR_TO_SLOT.get(11) == "morning"

    def test_noon_slots(self):
        assert act_sim._HOUR_TO_SLOT.get(12) == "noon"
        assert act_sim._HOUR_TO_SLOT.get(13) == "noon"

    def test_afternoon_slots(self):
        assert act_sim._HOUR_TO_SLOT.get(14) == "afternoon"
        assert act_sim._HOUR_TO_SLOT.get(16) == "afternoon"

    def test_evening_slots(self):
        assert act_sim._HOUR_TO_SLOT.get(19) == "evening"
        assert act_sim._HOUR_TO_SLOT.get(22) == "evening"

    def test_night_slots(self):
        assert act_sim._HOUR_TO_SLOT.get(23) == "night"
        assert act_sim._HOUR_TO_SLOT.get(0) == "night"
        assert act_sim._HOUR_TO_SLOT.get(3) == "night"


class TestGetCurrentActivity:
    """get_current_activity 函数测试。"""

    def test_returns_valid_activity(self):
        """返回有效的Activity对象。"""
        _reset_activity_cache()
        act = act_sim.get_current_activity()
        assert act is not None
        assert act.name
        assert act.action
        assert act.emoji

    def test_same_slot_returns_same_activity(self):
        """同时段多次调用返回相同活动。"""
        _reset_activity_cache()
        act1 = act_sim.get_current_activity()
        act2 = act_sim.get_current_activity()
        assert act1.name == act2.name

    def test_different_slot_switches_activity(self):
        """切换时段后活动变更。"""
        _reset_activity_cache()
        # 先获取一次初始化
        act_sim.get_current_activity()
        # 手动切换到不同时段
        act_sim._current_slot = "morning"
        act_sim._current_activity = act_sim.ACTIVITY_POOLS["morning"][0][0]

        # 然后清除当前slot触发切换
        act_sim._current_slot = "old_slot_nonexistent"
        act = act_sim.get_current_activity()
        # 应该刷新了
        assert act_sim._current_slot != "old_slot_nonexistent"


class TestGetActivityHint:
    """get_activity_hint 测试。"""

    def test_hint_contains_activity_info(self):
        """提示词包含活动名称。"""
        _reset_activity_cache()
        hint = act_sim.get_activity_hint()
        assert "你现在正在" in hint or "你在" in hint
        assert len(hint) > 5

    def test_hint_not_empty(self):
        """提示词不为空。"""
        _reset_activity_cache()
        hint = act_sim.get_activity_hint()
        assert len(hint) > 0


class TestNaturalMention:
    """get_natural_activity_mention 测试。"""

    def test_mostly_returns_empty(self):
        """大部分情况下返回空字符串（5%概率）。"""
        _reset_activity_cache()
        mentions = [act_sim.get_natural_activity_mention() for _ in range(200)]
        empty_count = sum(1 for m in mentions if m == "")
        # 5%概率，95%应该为空。允许一定误差
        assert empty_count >= 150, f"Expected most to be empty, got {empty_count}/200 non-empty"

    def test_when_not_empty_is_string(self):
        """非空结果是字符串。"""
        _reset_activity_cache()
        results = set()
        for _ in range(500):
            m = act_sim.get_natural_activity_mention()
            if m:
                results.add(m)
                assert isinstance(m, str)
                assert len(m) > 0


class TestGetDoingReply:
    """get_doing_reply 测试。"""

    def test_returns_string(self):
        _reset_activity_cache()
        reply = act_sim.get_doing_reply()
        assert isinstance(reply, str)
        assert len(reply) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
