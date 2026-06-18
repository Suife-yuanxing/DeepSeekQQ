"""Test Time Validator — LLM 回复时间校验。

覆盖：
- _is_time_valid 时段判断（含跨夜）
- validate_time_in_reply 各规则修正
- 小时数修正
"""
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════
# _is_time_valid — 时段合法性判断
# ═══════════════════════════════════════════════════════════════

class TestIsTimeValid:
    """测试 _is_time_valid 时段判断。"""

    def test_within_range(self):
        """当前小时在合法时段内应返回 True。"""
        from plugins.deepseek.time_validator import _is_time_valid
        assert _is_time_valid(8, 5, 11) is True   # 早上8点在 5-11 内
        assert _is_time_valid(14, 12, 18) is True  # 下午2点在 12-18 内

    def test_outside_range(self):
        """当前小时不在合法时段内应返回 False。"""
        from plugins.deepseek.time_validator import _is_time_valid
        assert _is_time_valid(3, 5, 11) is False   # 凌晨3点不在 5-11
        assert _is_time_valid(15, 20, 5) is False  # 下午3点不在跨夜 20-5

    def test_overnight_range(self):
        """跨夜时段（如 20-5）应正确判断。"""
        from plugins.deepseek.time_validator import _is_time_valid
        # 20-5 跨夜：22点、0点、3点合法，12点、15点不合法
        assert _is_time_valid(22, 20, 5) is True   # 晚上10点在跨夜范围
        assert _is_time_valid(3, 20, 5) is True    # 凌晨3点在跨夜范围
        assert _is_time_valid(12, 20, 5) is False  # 中午12点不在跨夜范围
        assert _is_time_valid(17, 20, 5) is False  # 下午5点不在跨夜范围

    def test_boundary_hours(self):
        """边界小时测试。"""
        from plugins.deepseek.time_validator import _is_time_valid
        assert _is_time_valid(5, 5, 11) is True    # 5点含
        assert _is_time_valid(10, 5, 11) is True   # 10点
        assert _is_time_valid(11, 5, 11) is False  # 11点不含（end exclusive）


# ═══════════════════════════════════════════════════════════════
# validate_time_in_reply — 时间校验主函数
# ═══════════════════════════════════════════════════════════════

class TestValidateTimeInReply:
    """测试 validate_time_in_reply 各种规则的修正。"""

    def test_no_change_when_valid(self):
        """合法时间的回复不应被修改。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        # 模拟早上8点：早安合法
        mock_now = datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("早安！今天天气真好")
            assert "早安" in result

    def test_strip_good_morning_at_night(self):
        """晚上说早安应被删除。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 22, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("早安呀~")
            # 早安在晚上22点不合法，应被 strip
            assert "早安" not in result

    def test_strip_good_night_at_morning(self):
        """早上说晚安应被删除。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("晚安啦，快去睡吧~")
            # 晚安在早上8点不合法
            assert "晚安" not in result

    def test_replace_dinner_at_morning(self):
        """早上说吃晚饭应替换为「吃饭」。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("吃晚饭了吗？")
            # 「吃晚饭」在早上不合法，应被替换为「吃饭」
            assert "晚饭" not in result

    def test_replace_breakfast_at_night(self):
        """晚上说吃早饭应替换为「吃东西」。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 22, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("吃早饭了吗？")
            assert "早饭" not in result

    def test_strip_afternoon_at_morning(self):
        """早上说下午好应被删除。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 8, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("下午好呀~")
            assert "下午好" not in result

    def test_empty_input(self):
        """空输入应返回空字符串。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        assert validate_time_in_reply("") == ""

    def test_hour_correction(self):
        """编造的小时数应被修正为实际时间。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        # 现在是14点，LLM 说了"都凌晨5点了"（差异>1小时）
        mock_now = datetime(2026, 6, 18, 14, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            text = "都凌晨5点了，你还没睡吗"
            result = validate_time_in_reply(text)
            # "凌晨"在14点不合法，会被 strip；5点会被修正为14点
            # 修正后的文本不应包含原来的 "5点"
            assert "5点" not in result

    def test_hour_within_tolerance_not_corrected(self):
        """±1小时内的小时数不应被修正。"""
        from plugins.deepseek.time_validator import validate_time_in_reply
        mock_now = datetime(2026, 6, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=8)))
        with patch("plugins.deepseek.time_validator.datetime") as mock_dt:
            mock_dt.now.return_value = mock_now
            result = validate_time_in_reply("都快11点了呢")
            # 11点和当前10点差1小时，在容差范围内
            assert "11点" in result
