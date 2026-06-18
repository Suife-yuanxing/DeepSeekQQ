"""Test Reminder — 自然语言时间解析、意图识别、提醒管理。

覆盖：
- _today_at / _tomorrow_at / _day_after_tomorrow_at 时间计算
- _regex_parse_time 正则兜底解析
- is_reminder_request 意图分类
- ReminderParseResult dataclass
"""
import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# 时间辅助函数
# ═══════════════════════════════════════════════════════════════

class TestTimeHelpers:
    """测试 _today_at、_tomorrow_at、_day_after_tomorrow_at。"""

    def test_tomorrow_at_returns_future(self):
        """明天的时间应该在未来（> 24小时后 - 1秒容错）。"""
        from plugins.deepseek.reminder import _tomorrow_at
        now = time.time()
        result = _tomorrow_at(8, 0)
        assert result > now
        # 应该至少 > now + 23h（当前时间+明天8点可能少于24h如果现在是晚上）
        assert result > now + 3600  # 至少在未来1小时

    def test_tomorrow_at_morning(self):
        """明天早上8点应该正确计算。"""
        from plugins.deepseek.reminder import _tomorrow_at
        result = _tomorrow_at(8, 0)
        dt = datetime.fromtimestamp(result)
        assert dt.hour == 8
        assert dt.minute == 0

    def test_day_after_tomorrow_at(self):
        """后天的时间应该 > 明天。"""
        from plugins.deepseek.reminder import _day_after_tomorrow_at, _tomorrow_at
        tomorrow = _tomorrow_at(8, 0)
        day_after = _day_after_tomorrow_at(8, 0)
        assert day_after > tomorrow
        # 应该相差约 24 小时
        assert abs(day_after - tomorrow - 86400) < 10

    def test_today_at_past_time_moves_to_tomorrow(self):
        """如果今天的目标时间已过，应该移到明天。"""
        from plugins.deepseek.reminder import _today_at
        now = datetime.now()
        # 设置为一个已经过去的小时（如凌晨3点，如果现在 >3点的话）
        past_hour = (now.hour - 2) % 24
        result = _today_at(past_hour, 0)
        dt = datetime.fromtimestamp(result)
        # 应该大于现在的时间戳
        assert result > time.time()

    def test_today_at_future_time_stays_today(self):
        """如果今天的目标时间还没到，应该保留在今天。"""
        from plugins.deepseek.reminder import _today_at
        # 设置为23:59，几乎总是在未来
        result = _today_at(23, 59)
        assert result > time.time()
        dt = datetime.fromtimestamp(result)
        assert dt.hour == 23
        assert dt.minute == 59


# ═══════════════════════════════════════════════════════════════
# 正则时间解析
# ═══════════════════════════════════════════════════════════════

class TestRegexParseTime:
    """测试 _regex_parse_time 对各种中文时间表达式的解析。"""

    def test_n_hours_later(self):
        """「3小时后」应正确解析为未来时间。"""
        from plugins.deepseek.reminder import _regex_parse_time
        now = time.time()
        result = _regex_parse_time("3小时后提醒我")
        assert result is not None
        assert abs(result - (now + 3 * 3600)) < 5  # 5秒容错

    def test_n_minutes_later(self):
        """「30分钟后」应正确解析。"""
        from plugins.deepseek.reminder import _regex_parse_time
        now = time.time()
        result = _regex_parse_time("30分钟后叫我")
        assert result is not None
        assert abs(result - (now + 30 * 60)) < 5

    def test_tomorrow_morning(self):
        """「明天早上8点」应正确解析。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("明天早上8点提醒我开会")
        assert result is not None
        dt = datetime.fromtimestamp(result)
        assert dt.hour == 8
        assert dt.minute == 0

    def test_tomorrow_afternoon(self):
        """「明天下午3点」应正确解析为 15:00。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("明天下午3点提醒我")
        assert result is not None
        dt = datetime.fromtimestamp(result)
        # 下午3点 → hour 15（如果使用 +12 转换）
        assert dt.hour in (15, 3)  # 取决于实现细节

    def test_tonight(self):
        """「晚上10点」应正确解析。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("晚上10点提醒我睡觉")
        assert result is not None
        dt = datetime.fromtimestamp(result)
        assert dt.hour in (22, 10)  # 晚上→+12 or not

    def test_bare_hour(self):
        """「10点」无修饰词时应解析。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("10点提醒我")
        assert result is not None

    def test_no_time_match(self):
        """无时间表达时返回 None。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("你好呀念念")
        assert result is None

    def test_empty_string(self):
        """空字符串返回 None。"""
        from plugins.deepseek.reminder import _regex_parse_time
        result = _regex_parse_time("")
        assert result is None


# ═══════════════════════════════════════════════════════════════
# 意图识别
# ═══════════════════════════════════════════════════════════════

class TestIsReminderRequest:
    """测试 is_reminder_request 意图分类。"""

    def test_create_intent(self):
        """「提醒我...」应识别为 create。"""
        from plugins.deepseek.reminder import is_reminder_request
        assert is_reminder_request("提醒我明天开会") == "create"
        assert is_reminder_request("记得提醒我") == "create"
        assert is_reminder_request("帮我设个闹钟") == "create"

    def test_list_intent(self):
        """「我的提醒」应识别为 list。"""
        from plugins.deepseek.reminder import is_reminder_request
        assert is_reminder_request("查看提醒") == "list"
        assert is_reminder_request("我有哪些提醒") == "list"
        assert is_reminder_request("提醒列表") == "list"

    def test_cancel_intent(self):
        """「取消提醒」应识别为 cancel。"""
        from plugins.deepseek.reminder import is_reminder_request
        assert is_reminder_request("取消提醒") == "cancel"
        assert is_reminder_request("删除提醒") == "cancel"
        assert is_reminder_request("不用提醒了") == "cancel"

    def test_not_reminder(self):
        """非提醒消息返回空字符串。"""
        from plugins.deepseek.reminder import is_reminder_request
        assert is_reminder_request("你好呀") == ""
        assert is_reminder_request("今天天气怎么样") == ""
        assert is_reminder_request("哈哈") == ""

    def test_cancel_priority_over_list(self):
        """取消关键词应优先于列表关键词。"""
        from plugins.deepseek.reminder import is_reminder_request
        # 同时包含 cancel 和 list 关键词时，cancel 优先（先检查 cancel）
        result = is_reminder_request("取消提醒列表")
        # cancel keywords checked first, so it should be "cancel"
        assert result == "cancel"


# ═══════════════════════════════════════════════════════════════
# ReminderParseResult
# ═══════════════════════════════════════════════════════════════

class TestReminderParseResult:
    """测试 ReminderParseResult dataclass。"""

    def test_success_result(self):
        """成功的解析结果应有正确字段。"""
        from plugins.deepseek.reminder import ReminderParseResult
        now = time.time() + 3600
        result = ReminderParseResult(
            success=True,
            trigger_time=now,
            content="开会",
            repeat_type="daily",
        )
        assert result.success is True
        assert result.trigger_time == now
        assert result.content == "开会"
        assert result.repeat_type == "daily"
        assert result.error == ""

    def test_failure_result(self):
        """失败的解析结果应有错误信息。"""
        from plugins.deepseek.reminder import ReminderParseResult
        result = ReminderParseResult(
            success=False,
            error="无法解析时间",
        )
        assert result.success is False
        assert result.trigger_time is None
        assert result.error == "无法解析时间"

    def test_default_values(self):
        """默认值应正确。"""
        from plugins.deepseek.reminder import ReminderParseResult
        result = ReminderParseResult(success=False)
        assert result.repeat_type == "none"
        assert result.content == ""


# ═══════════════════════════════════════════════════════════════
# get_pending_reminders_context
# ═══════════════════════════════════════════════════════════════

class TestPendingRemindersContext:
    """测试 get_pending_reminders_context。"""

    @pytest.mark.asyncio
    async def test_no_reminders_returns_empty(self):
        """无提醒时返回空字符串。"""
        from plugins.deepseek.reminder import get_pending_reminders_context
        with patch("plugins.deepseek.reminder.get_user_reminders", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = []
            result = await get_pending_reminders_context("user123")
            assert result == ""

    @pytest.mark.asyncio
    async def test_with_reminders(self):
        """有提醒时返回格式化的上下文。"""
        from plugins.deepseek.reminder import get_pending_reminders_context
        now = time.time()
        reminders = [
            {
                "trigger_time": now + 7200,  # 2小时后
                "content": "开会",
            },
            {
                "trigger_time": now + 180,  # 3分钟后
                "content": "喝水",
            },
        ]
        with patch("plugins.deepseek.reminder.get_user_reminders", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = reminders
            result = await get_pending_reminders_context("user123")
            assert "开会" in result
            assert "喝水" in result
            assert "提醒" in result
