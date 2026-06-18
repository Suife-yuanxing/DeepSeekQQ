"""Test World Context — 现实世界感知（天气、时间、季节）。

覆盖：
- get_time_of_day 时间段划分
- get_season 季节判断
- get_weather_suggestion 建议生成
- extract_city_from_message 城市提取
- _wmo_to_condition WMO 天气码转换
- _wind_deg_to_dir 风向转换
"""
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from unittest.mock import patch

import pytest


# ═══════════════════════════════════════════════════════════════
# get_time_of_day — 时间段划分
# ═══════════════════════════════════════════════════════════════
# 注意：get_time_of_day() 使用函数内 `from datetime import datetime`，
# 需要 patch stdlib datetime.datetime

class TestGetTimeOfDay:
    """测试 get_time_of_day 各种时段。"""

    def _set_hour(self, hour):
        """创建指定小时的 mock datetime。"""
        return datetime(2026, 6, 18, hour, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_early_morning(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(6)
            assert get_time_of_day() == "清晨"

    def test_morning(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(10)
            assert get_time_of_day() == "上午"

    def test_noon(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(13)
            assert get_time_of_day() == "中午"

    def test_afternoon(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(15)
            assert get_time_of_day() == "午后"

    def test_evening(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(18)
            assert get_time_of_day() == "傍晚"

    def test_night(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(21)
            assert get_time_of_day() == "晚上"

    def test_late_night(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(23)
            assert get_time_of_day() == "深夜"

    def test_dawn(self):
        from plugins.deepseek.world_context import get_time_of_day
        with patch("datetime.datetime") as mock_dt:
            mock_dt.now.return_value = self._set_hour(3)
            assert get_time_of_day() == "凌晨"


# ═══════════════════════════════════════════════════════════════
# get_season — 季节判断
# ═══════════════════════════════════════════════════════════════
# 注意：get_season() 同样使用函数内 import datetime

class TestGetSeason:
    """测试 get_season 季节划分。"""

    def _set_month(self, month):
        return datetime(2026, month, 15, 12, 0, 0, tzinfo=timezone(timedelta(hours=8)))

    def test_spring(self):
        from plugins.deepseek.world_context import get_season
        for m in (3, 4, 5):
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = self._set_month(m)
                assert get_season() == "春天", f"Month {m} should be spring"

    def test_summer(self):
        from plugins.deepseek.world_context import get_season
        for m in (6, 7, 8):
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = self._set_month(m)
                assert get_season() == "夏天", f"Month {m} should be summer"

    def test_autumn(self):
        from plugins.deepseek.world_context import get_season
        for m in (9, 10, 11):
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = self._set_month(m)
                assert get_season() == "秋天", f"Month {m} should be autumn"

    def test_winter(self):
        from plugins.deepseek.world_context import get_season
        for m in (12, 1, 2):
            with patch("datetime.datetime") as mock_dt:
                mock_dt.now.return_value = self._set_month(m)
                assert get_season() == "冬天", f"Month {m} should be winter"


# ═══════════════════════════════════════════════════════════════
# WMO 天气码转换
# ═══════════════════════════════════════════════════════════════

class TestWmoToCondition:
    """测试 _wmo_to_condition WMO 天气码→中文描述。"""

    def test_clear_sky(self):
        from plugins.deepseek.world_context import _wmo_to_condition
        assert _wmo_to_condition(0) == "晴天"

    def test_cloudy(self):
        from plugins.deepseek.world_context import _wmo_to_condition
        assert _wmo_to_condition(1) == "多云"
        assert _wmo_to_condition(2) == "多云"
        assert _wmo_to_condition(3) == "多云"

    def test_light_rain(self):
        from plugins.deepseek.world_context import _wmo_to_condition
        assert _wmo_to_condition(51) == "小雨"

    def test_heavy_rain(self):
        from plugins.deepseek.world_context import _wmo_to_condition
        assert _wmo_to_condition(66) == "暴雨"

    def test_unknown_code(self):
        from plugins.deepseek.world_context import _wmo_to_condition
        result = _wmo_to_condition(999)
        assert "未知" in result
        assert "999" in result


# ═══════════════════════════════════════════════════════════════
# 风向转换
# ═══════════════════════════════════════════════════════════════

class TestWindDegToDir:
    """测试 _wind_deg_to_dir 风向角度→中文。"""

    def test_north(self):
        from plugins.deepseek.world_context import _wind_deg_to_dir
        assert _wind_deg_to_dir(0) == "北"
        assert _wind_deg_to_dir(360) == "北"

    def test_east(self):
        from plugins.deepseek.world_context import _wind_deg_to_dir
        assert _wind_deg_to_dir(90) == "东"

    def test_south(self):
        from plugins.deepseek.world_context import _wind_deg_to_dir
        assert _wind_deg_to_dir(180) == "南"

    def test_west(self):
        from plugins.deepseek.world_context import _wind_deg_to_dir
        assert _wind_deg_to_dir(270) == "西"

    def test_northeast(self):
        from plugins.deepseek.world_context import _wind_deg_to_dir
        dir_name = _wind_deg_to_dir(45)
        assert "东北" in dir_name or "东北" == dir_name


# ═══════════════════════════════════════════════════════════════
# get_weather_suggestion — 天气建议
# ═══════════════════════════════════════════════════════════════

class TestWeatherSuggestion:
    """测试 get_weather_suggestion 建议生成。"""

    def test_hot_weather(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="晴天", temp="36")
        result = get_weather_suggestion(w)
        assert "热" in result or "水" in result

    def test_cold_weather(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="小雪", temp="-3")
        result = get_weather_suggestion(w)
        assert "冷" in result or "冻" in result or "保暖" in result

    def test_rain_suggestion(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="中雨", temp="15")
        result = get_weather_suggestion(w)
        assert "伞" in result

    def test_snow_suggestion(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="大雪", temp="-1")
        result = get_weather_suggestion(w)
        assert "滑" in result or "雪" in result or "小心" in result

    def test_mild_weather_no_suggestion(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="多云", temp="22")
        result = get_weather_suggestion(w)
        # 温和天气，无特殊建议
        assert result == "" or "今天" not in result.lower()

    def test_empty_weather(self):
        from plugins.deepseek.world_context import get_weather_suggestion
        assert get_weather_suggestion(None) == ""

    def test_invalid_temp(self):
        from plugins.deepseek.world_context import get_weather_suggestion, WeatherInfo
        w = WeatherInfo(condition="晴天", temp="--")
        result = get_weather_suggestion(w)
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# extract_city_from_message — 城市提取
# ═══════════════════════════════════════════════════════════════

class TestExtractCityFromMessage:
    """测试 extract_city_from_message 城市识别。"""

    def test_extract_known_city(self):
        from plugins.deepseek.world_context import extract_city_from_message
        result = extract_city_from_message("我在上海")
        assert result == "上海"

    def test_extract_weather_query_city(self):
        from plugins.deepseek.world_context import extract_city_from_message
        result = extract_city_from_message("杭州天气怎么样")
        assert result == "杭州"

    def test_extract_today_query_city(self):
        from plugins.deepseek.world_context import extract_city_from_message
        result = extract_city_from_message("北京今天多少度")
        assert result == "北京"

    def test_no_city(self):
        from plugins.deepseek.world_context import extract_city_from_message
        result = extract_city_from_message("今天天气怎么样")
        assert result is None

    def test_unknown_city(self):
        from plugins.deepseek.world_context import extract_city_from_message
        # 不在已知城市列表中的应返回 None
        result = extract_city_from_message("我在火星")
        assert result is None
