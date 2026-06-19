# -*- coding: utf-8 -*-
"""CausalContext 测试 — 因果上下文总线的核心功能。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from datetime import datetime
from unittest.mock import patch

from plugins.deepseek.causal_context import (
    CausalContext,
    CausalEvent,
    get_cc,
    get_cc_safe,
    remove_cc,
    reset_all_cc,
    set_virtual_time_provider,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _cleanup():
    """每个测试前清理 CausalContext 注册表。"""
    reset_all_cc()
    yield
    reset_all_cc()


class TestCausalContext:
    """CausalContext 数据类核心功能。"""

    def test_init_defaults(self):
        cc = CausalContext(session_id="test_session")
        assert cc.current_activity == ""
        assert cc.activity_intensity == 0.5
        assert cc.activity_can_interrupt is True
        assert cc.energy == 1.0
        assert cc.hunger == 0.0
        assert cc.tiredness == 0.0
        assert cc.current_emotion == "平静"
        assert cc.emotion_intensity == 0.0
        assert cc.fatigue_level == 0
        assert cc.is_ending is False
        assert cc.is_absent is False
        assert cc.causal_chain == []

    def test_update_activity(self):
        cc = CausalContext(session_id="test")
        cc.update_activity("打游戏", intensity=0.8, can_interrupt=False)
        assert cc.current_activity == "打游戏"
        assert cc.activity_intensity == 0.8
        assert cc.activity_can_interrupt is False

    def test_update_activity_causal_event(self):
        cc = CausalContext(session_id="test")
        cc.update_activity("上课", intensity=0.7, can_interrupt=True)
        cc.update_activity("打游戏", intensity=0.8, can_interrupt=False)
        # 活动切换应产生因果事件
        events = cc.get_events_by_source("activity_sim")
        assert len(events) == 1  # 只有切换时产生事件
        assert "上课" in events[0].cause
        assert "打游戏" in events[0].cause

    def test_update_emotion(self):
        cc = CausalContext(session_id="test")
        cc.update_emotion("生气", intensity=0.7, valence=-0.6, arousal=0.5)
        assert cc.current_emotion == "生气"
        assert cc.emotion_intensity == 0.7
        assert cc.emotion_valence == -0.6
        assert cc.emotion_arousal == 0.5

    def test_update_emotion_causal_event(self):
        cc = CausalContext(session_id="test")
        cc.update_emotion("平静", intensity=0.0)
        cc.update_emotion("开心", intensity=0.6, valence=0.5, arousal=0.4)
        events = cc.get_events_by_source("emotion_deep")
        assert len(events) == 1
        assert "平静" in events[0].cause
        assert "开心" in events[0].cause

    def test_update_body_state(self):
        cc = CausalContext(session_id="test")
        cc.update_body_state(energy=0.5, hunger=0.3, tiredness=0.7, schedule_period="sleeping")
        assert cc.energy == 0.5
        assert cc.hunger == 0.3
        assert cc.tiredness == 0.7
        assert cc.schedule_period == "sleeping"

    def test_update_body_state_partial(self):
        cc = CausalContext(session_id="test")
        cc.update_body_state(energy=0.3)  # 只更新 energy
        assert cc.energy == 0.3
        assert cc.hunger == 0.0  # 其他保持不变
        assert cc.tiredness == 0.0

    def test_update_fatigue(self):
        cc = CausalContext(session_id="test")
        cc.update_fatigue(level=2, is_ending=False)
        assert cc.fatigue_level == 2
        assert cc.is_ending is False

    def test_update_fatigue_causal_event(self):
        cc = CausalContext(session_id="test")
        cc.update_fatigue(level=3, is_ending=True)
        events = cc.get_events_by_source("conversation_fatigue")
        assert len(events) == 1
        assert "Lv.3" in events[0].cause

    def test_set_absent(self):
        cc = CausalContext(session_id="test")
        import time
        until = time.time() + 1800
        cc.set_absent("在打游戏", until)
        assert cc.is_absent is True
        assert cc.absence_reason == "在打游戏"
        assert cc.absence_until == until

    def test_set_absent_causal_event(self):
        cc = CausalContext(session_id="test")
        import time
        cc.set_absent("在午睡", time.time() + 3600)
        events = cc.get_events_by_source("absence_events")
        assert len(events) == 1
        assert "午睡" in events[0].cause

    def test_clear_absent(self):
        cc = CausalContext(session_id="test")
        import time
        cc.set_absent("手机没电", time.time() + 3600)
        reason = cc.clear_absent()
        assert reason == "手机没电"
        assert cc.is_absent is False
        assert cc.absence_reason == ""

    def test_virtual_time(self):
        cc = CausalContext(session_id="test")
        assert isinstance(cc.virtual_time, datetime)
        assert cc.virtual_hour == cc.virtual_time.hour
        assert cc.virtual_weekday == cc.virtual_time.weekday()

    def test_virtual_is_weekend(self):
        cc = CausalContext(session_id="test")
        # 只是类型检查，具体值取决于当前时间
        assert isinstance(cc.virtual_is_weekend, bool)

    def test_update_virtual_time(self):
        cc = CausalContext(session_id="test")
        new_time = datetime(2026, 6, 22, 14, 30, 0)
        cc.update_virtual_time(new_time)
        assert cc.virtual_hour == 14
        assert cc.virtual_time.minute == 30

    def test_get_summary(self):
        cc = CausalContext(session_id="test")
        cc.update_activity("打游戏", intensity=0.8)
        cc.update_emotion("开心", intensity=0.5)
        summary = cc.get_summary()
        assert "打游戏" in summary
        assert "开心" in summary


class TestCausalChain:
    """因果链功能测试。"""

    def test_chain_max_length(self):
        cc = CausalContext(session_id="test")
        # 插入超过上限的事件
        for i in range(150):
            cc.update_activity(f"活动{i}", intensity=0.5)
        assert len(cc.causal_chain) <= 100

    def test_get_recent_events(self):
        cc = CausalContext(session_id="test")
        for i in range(5):
            cc.update_activity(f"活动{i}", intensity=0.5)
        recent = cc.get_recent_events(3)
        assert len(recent) == 3
        assert "活动4" in recent[-1].cause

    def test_get_events_by_source(self):
        cc = CausalContext(session_id="test")
        cc.update_activity("活动A", intensity=0.5)  # 初始设置，无 old→不产生事件
        cc.update_emotion("开心", intensity=0.5)     # 初始设置，无 old→不产生事件
        cc.update_activity("活动B", intensity=0.5)   # 切换→产生事件
        cc.update_emotion("难过", intensity=0.6)     # 切换→产生事件
        activity_events = cc.get_events_by_source("activity_sim")
        emotion_events = cc.get_events_by_source("emotion_deep")
        assert len(activity_events) == 1  # 仅切换时产生事件
        assert len(emotion_events) == 2  # 平静→开心 + 开心→难过，共两次切换
        assert "活动B" in activity_events[0].cause
        # 第一个情绪事件：平静→开心，第二个：开心→难过
        all_causes = " ".join(e.cause for e in emotion_events)
        assert "开心" in all_causes
        assert "难过" in all_causes

    def test_event_repr(self):
        event = CausalEvent(
            timestamp=1000000.0,
            source="test_source",
            cause="测试原因",
            effect="测试效果",
        )
        repr_str = repr(event)
        assert "test_source" in repr_str
        assert "测试原因" in repr_str

    def test_reset_clears_chain(self):
        cc = CausalContext(session_id="test")
        cc.update_activity("活动", intensity=0.5)
        cc.update_emotion("开心", intensity=0.5)
        cc.reset()
        assert len(cc.causal_chain) == 0
        assert cc.current_emotion == "平静"
        assert cc.current_activity == ""


class TestCausalContextRegistry:
    """get_cc / remove_cc / get_cc_safe 测试。"""

    def test_get_cc_creates_instance(self):
        cc = get_cc("session_1")
        assert cc.session_id == "session_1"
        assert isinstance(cc, CausalContext)

    def test_get_cc_returns_same_instance(self):
        cc1 = get_cc("session_2")
        cc2 = get_cc("session_2")
        assert cc1 is cc2  # 同一会话返回同一实例

    def test_get_cc_different_sessions(self):
        cc_a = get_cc("session_a")
        cc_b = get_cc("session_b")
        assert cc_a is not cc_b

    def test_remove_cc(self):
        get_cc("session_3")
        remove_cc("session_3")
        assert get_cc_safe("session_3") is None

    def test_get_cc_safe_nonexistent(self):
        assert get_cc_safe("nonexistent_session") is None

    def test_get_cc_updates_virtual_time(self):
        with patch("plugins.deepseek.causal_context._get_virtual_now") as mock_time:
            mock_time.return_value = datetime(2026, 6, 22, 10, 0, 0)
            cc = get_cc("session_time")
            assert cc.virtual_hour == 10

    def test_reset_all_cc(self):
        get_cc("s1")
        get_cc("s2")
        reset_all_cc()
        assert get_cc_safe("s1") is None
        assert get_cc_safe("s2") is None

    def test_get_active_sessions(self):
        get_cc("s1")
        get_cc("s2")
        sessions = get_cc("s3")  # noqa: F841
        active = sorted([s for s in [get_cc_safe("s1"), get_cc_safe("s2"), get_cc_safe("s3")] if s is not None], key=lambda x: x.session_id)
        assert len(active) == 3


class TestVirtualTimeProvider:
    """虚拟时间提供者测试。"""

    def test_set_virtual_time_provider(self):
        mock_time = datetime(2026, 7, 1, 8, 0, 0)
        set_virtual_time_provider(lambda: mock_time)
        cc = get_cc("test_vt")
        assert cc.virtual_hour == 8
        assert cc.virtual_time == mock_time

    def test_reset_clears_provider(self):
        mock_time = datetime(2026, 7, 1, 8, 0, 0)
        set_virtual_time_provider(lambda: mock_time)
        reset_all_cc()
        # 重置后应使用真实时间
        cc = get_cc("test_reset")
        assert isinstance(cc.virtual_time, datetime)
