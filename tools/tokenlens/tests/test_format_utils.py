"""format_utils.py 单元测试"""

from tools.tokenlens.format_utils import (
    format_cost,
    format_tokens,
    format_tokens_short,
    get_period_boundary,
    is_in_period,
    parse_timestamp,
    short_id,
)


class TestFormatTokens:
    def test_format_tokens_thousands(self):
        assert format_tokens(1234) == "1,234"

    def test_format_tokens_millions(self):
        assert format_tokens(12345678) == "12,345,678"

    def test_format_tokens_billions(self):
        assert format_tokens(2001359360) == "2,001,359,360"

    def test_format_tokens_zero(self):
        assert format_tokens(0) == "0"


class TestFormatTokensShort:
    def test_K(self):
        assert format_tokens_short(2000) == "2K"

    def test_K_boundary(self):
        assert format_tokens_short(999) == "999"
        assert format_tokens_short(1000) == "1K"
        assert format_tokens_short(999999) == "1000K"

    def test_M(self):
        assert format_tokens_short(1_500_000) == "1.5M"

    def test_B(self):
        assert format_tokens_short(2_001_359_360) == "2.00B"

    def test_zero(self):
        assert format_tokens_short(0) == "0"


class TestFormatCost:
    def test_format_cost_desktop(self):
        assert "¥" in format_cost(127.34)

    def test_format_cost_mobile(self):
        result = format_cost(127.34, mobile=True)
        assert "¥" in result
        assert "." not in result  # 无小数

    def test_format_cost_zero(self):
        result = format_cost(0)
        assert "¥0.00" in result


class TestTimestamp:
    def test_parse_iso(self):
        dt = parse_timestamp("2026-06-15T10:30:00")
        assert dt is not None

    def test_parse_utc_z(self):
        dt = parse_timestamp("2026-06-15T10:30:00Z")
        assert dt is not None

    def test_parse_utc_offset(self):
        dt = parse_timestamp("2026-06-15T10:30:00+08:00")
        assert dt is not None

    def test_parse_invalid(self):
        dt = parse_timestamp("not-a-date")
        assert dt is None

    def test_parse_none(self):
        dt = parse_timestamp(None)  # type: ignore
        assert dt is None

    def test_parse_empty(self):
        dt = parse_timestamp("")
        assert dt is None

    def test_is_in_period_day(self):
        """今天的时间戳应在 period=day 内"""
        # 使用当前 UTC 时间（保证在 day 范围内）
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        assert is_in_period(now, "day")

    def test_is_in_period_old(self):
        """很旧的时间戳不应在 day 内"""
        assert not is_in_period("2020-01-01T00:00:00Z", "day")

    def test_is_in_period_week(self):
        """3 天前的应在 week 内"""
        from datetime import datetime, timedelta, timezone
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        assert is_in_period(three_days_ago, "week") is True


class TestPeriodBoundary:
    def test_day(self):
        boundary = get_period_boundary("day", tz_offset=8)
        assert boundary is not None

    def test_week(self):
        boundary = get_period_boundary("week", tz_offset=8)
        assert boundary is not None

    def test_month(self):
        boundary = get_period_boundary("month", tz_offset=8)
        assert boundary is not None

    def test_3month(self):
        boundary = get_period_boundary("3month", tz_offset=8)
        assert boundary is not None

    def test_year(self):
        boundary = get_period_boundary("year", tz_offset=8)
        assert boundary is not None

    def test_default(self):
        """未知 period 默认 week"""
        boundary = get_period_boundary("unknown", tz_offset=8)
        assert boundary is not None


class TestShortId:
    def test_default_length(self):
        assert short_id("abcdef1234567890") == "abcdef12"

    def test_custom_length(self):
        assert short_id("abcdef1234567890", 4) == "abcd"

    def test_shorter_than_n(self):
        assert short_id("abc", 8) == "abc"

    def test_empty(self):
        assert short_id("") == ""
