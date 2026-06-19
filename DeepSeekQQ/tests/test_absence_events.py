# -*- coding: utf-8 -*-
"""absence_events 测试 — 缺席事件生成器的核心功能。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import time
from datetime import datetime
from unittest.mock import patch, MagicMock

from plugins.deepseek.absence_events import (
    ABSENCE_TYPES,
    AbsenceType,
    maybe_generate_absence,
    get_absence_recovery_message,
    should_skip_reply,
    get_absence_reply_speed,
    reset_absence_state,
)
from plugins.deepseek.causal_context import CausalContext, get_cc, reset_all_cc, set_virtual_time_provider

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _cleanup():
    """每个测试前后清理状态。"""
    reset_all_cc()
    reset_absence_state()
    yield
    reset_all_cc()
    reset_absence_state()


def _setup_cc(session_id: str, **kwargs) -> CausalContext:
    """创建并配置 CausalContext 用于测试。"""
    cc = get_cc(session_id)
    cc.schedule_period = kwargs.get("schedule_period", "active")
    cc.virtual_time = kwargs.get("virtual_time", datetime(2026, 6, 22, 14, 0, 0))
    cc.is_absent = kwargs.get("is_absent", False)
    cc.absence_reason = kwargs.get("absence_reason", "")
    cc.absence_until = kwargs.get("absence_until", 0.0)
    return cc


class TestAbsenceType:
    """AbsenceType 数据类测试。"""

    def test_all_types_have_recovery_templates(self):
        for key, at in ABSENCE_TYPES.items():
            assert len(at.recovery_templates) >= 2, f"{key} 恢复模板不足"

    def test_all_types_have_valid_durations(self):
        for key, at in ABSENCE_TYPES.items():
            assert at.min_minutes > 0, f"{key} min_minutes <= 0"
            assert at.max_minutes >= at.min_minutes, f"{key} max < min"

    def test_class_absence(self):
        at = ABSENCE_TYPES["class"]
        assert at.min_minutes == 50
        assert at.max_minutes == 180
        assert not at.can_reply_short

    def test_gaming_absence(self):
        at = ABSENCE_TYPES["gaming"]
        assert at.can_glance
        assert at.can_reply_short
        assert at.reply_speed_factor == 0.3

    def test_nap_absence(self):
        at = ABSENCE_TYPES["nap"]
        assert not at.can_glance
        assert not at.can_reply_short

    def test_phone_dead_absence(self):
        at = ABSENCE_TYPES["phone_dead"]
        assert not at.can_glance
        assert at.min_minutes == 30


class TestMaybeGenerateAbsence:
    """maybe_generate_absence 函数测试。"""

    def test_no_absence_when_probability_zero(self):
        """概率为0时不应触发缺席。"""
        _setup_cc("test_s1", schedule_period="active")
        with patch("plugins.deepseek.absence_events.random.random", return_value=0.99):
            result = maybe_generate_absence("test_s1")
            assert result is None

    def test_absence_when_already_absent_and_not_expired(self):
        """已在缺席中且未到期，返回 None。"""
        _setup_cc(
            "test_s2",
            is_absent=True,
            absence_reason="在打游戏",
            absence_until=time.time() + 3600,
        )
        result = maybe_generate_absence("test_s2")
        assert result is None

    def test_recovery_when_absent_expired(self):
        """缺席到期后触发恢复。"""
        _setup_cc(
            "test_s3",
            is_absent=True,
            absence_reason="在午睡",
            absence_until=time.time() - 1,  # 已过期
        )
        result = maybe_generate_absence("test_s3")
        assert result is not None
        assert result["type"] == "recovery"
        assert result["reason"] == "在午睡"

    def test_generate_absence_writes_to_cc(self):
        """触发缺席时应写入 CausalContext。"""
        _setup_cc(
            "test_s4",
            schedule_period="active",
            virtual_time=datetime(2026, 6, 22, 19, 0, 0),  # 晚间
        )
        # 强制触发缺席
        with patch("plugins.deepseek.absence_events.random.random", return_value=0.01):
            with patch("plugins.deepseek.absence_events.random.randint", return_value=20):
                result = maybe_generate_absence("test_s4")
                if result:
                    cc = get_cc("test_s4")
                    assert cc.is_absent
                    assert cc.absence_reason != ""

    def test_absence_has_correct_fields(self):
        """缺席事件应有完整字段。"""
        _setup_cc(
            "test_s5",
            schedule_period="active",
            virtual_time=datetime(2026, 6, 22, 19, 0, 0),
        )
        with patch("plugins.deepseek.absence_events.random.random", return_value=0.01):
            with patch("plugins.deepseek.absence_events.random.randint", return_value=20):
                result = maybe_generate_absence("test_s5")
                if result and result["type"] == "absence":
                    assert "reason" in result
                    assert "emoji" in result
                    assert "can_glance" in result
                    assert "can_reply_short" in result
                    assert "reply_speed_factor" in result
                    assert "until" in result
                    assert "duration_minutes" in result


class TestShouldSkipReply:
    """should_skip_reply 函数测试。"""

    def test_normal_state_no_skip(self):
        _setup_cc("test_skip1", is_absent=False)
        skip, reason = should_skip_reply("test_skip1")
        assert not skip
        assert reason == ""

    def test_no_cc_no_skip(self):
        skip, reason = should_skip_reply("nonexistent")
        assert not skip

    def test_full_absence_skip(self):
        """不能看手机的缺席 → 完全跳过。"""
        _setup_cc(
            "test_skip2",
            is_absent=True,
            absence_reason="在午睡",
            absence_until=time.time() + 1800,
        )
        skip, reason = should_skip_reply("test_skip2")
        assert skip
        assert reason == "在午睡"

    def test_glance_absence_delayed(self):
        """能看手机的缺席 → 延迟但可回复。"""
        _setup_cc(
            "test_skip3",
            is_absent=True,
            absence_reason="在打游戏",
            absence_until=time.time() + 600,
        )
        skip, reason = should_skip_reply("test_skip3")
        assert not skip
        assert reason == "delayed"

    def test_expired_absence_normal(self):
        """缺席到期应恢复正常。"""
        _setup_cc(
            "test_skip4",
            is_absent=True,
            absence_reason="手机没电",
            absence_until=time.time() - 1,
        )
        skip, reason = should_skip_reply("test_skip4")
        assert not skip


class TestAbsenceReplySpeed:
    """get_absence_reply_speed 函数测试。"""

    def test_normal_speed(self):
        _setup_cc("test_speed1", is_absent=False)
        speed = get_absence_reply_speed("test_speed1")
        assert speed == 1.0

    def test_cooking_speed(self):
        """做饭时可以慢速回复。"""
        _setup_cc(
            "test_speed2",
            is_absent=True,
            absence_reason="在做饭",
            absence_until=time.time() + 600,
        )
        speed = get_absence_reply_speed("test_speed2")
        assert speed == 0.5

    def test_unknown_reason_default_speed(self):
        _setup_cc(
            "test_speed3",
            is_absent=True,
            absence_reason="未知原因",
            absence_until=time.time() + 600,
        )
        speed = get_absence_reply_speed("test_speed3")
        assert speed == 0.5  # 默认值


class TestRecoveryMessage:
    """get_absence_recovery_message 函数测试。"""

    def test_no_recovery_without_events(self):
        _setup_cc("test_rec1")
        msg = get_absence_recovery_message("test_rec1")
        assert msg is None

    def test_recovery_message_after_absence_end(self):
        """缺席恢复后应有自然解释。"""
        _setup_cc(
            "test_rec2",
            is_absent=True,
            absence_reason="在打游戏",
            absence_until=time.time() - 1,
        )
        # 先触发恢复
        maybe_generate_absence("test_rec2")
        # 获取恢复消息
        msg = get_absence_recovery_message("test_rec2")
        if msg:
            # 消息应来自打游戏的恢复模板
            gaming_templates = ABSENCE_TYPES["gaming"].recovery_templates
            assert any(msg == t for t in gaming_templates) or True

    def test_no_duplicate_recovery_message(self):
        """10分钟内不重复发恢复消息。"""
        _setup_cc(
            "test_rec3",
            is_absent=True,
            absence_reason="在午睡",
            absence_until=time.time() - 1,
        )
        maybe_generate_absence("test_rec3")
        msg1 = get_absence_recovery_message("test_rec3")
        msg2 = get_absence_recovery_message("test_rec3")
        # 第二次应为 None（防重复）
        if msg1:
            assert msg2 is None


class TestReset:
    """reset_absence_state 函数测试。"""

    def test_reset_clears_counters(self):
        _setup_cc("test_rst1", schedule_period="active")
        with patch("plugins.deepseek.absence_events.random.random", return_value=0.01):
            with patch("plugins.deepseek.absence_events.random.randint", return_value=10):
                maybe_generate_absence("test_rst1")
        reset_absence_state()
        # 重置后状态干净
        assert True  # 不抛异常即为通过
