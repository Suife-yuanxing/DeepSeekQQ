"""行为模式丰富测试 — 天气驱动、季节愿望、随机行为、活跃度波动。"""
import os
import pytest
from unittest.mock import patch
from datetime import datetime


# ============================================================
# 天气驱动行为测试
# ============================================================

class TestWeatherBehavior:
    def test_rain_triggers_reaction(self):
        """下雨应触发反应"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        # 100% 概率触发
        result = get_weather_behavior("小雨", "15", trigger_chance=1.0)
        assert result is not None
        assert any(kw in result for kw in ["雨", "伞", "窝", "睡觉"])

    def test_snow_triggers_reaction(self):
        """下雪应触发反应"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        result = get_weather_behavior("大雪", "-3", trigger_chance=1.0)
        assert result is not None
        assert any(kw in result for kw in ["雪", "冷", "白"])

    def test_hot_weather_triggers(self):
        """高温应触发反应"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        # 用不匹配任何 trigger 的 condition，让温度判断生效
        result = get_weather_behavior("未知天气", "35", trigger_chance=1.0)
        assert result is not None
        assert any(kw in result for kw in ["热", "冰", "空调", "游泳", "融化", "躺着"])

    def test_cold_weather_triggers(self):
        """低温应触发反应"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        # 用不匹配任何 trigger 的 condition，让温度判断生效
        result = get_weather_behavior("未知天气", "2", trigger_chance=1.0)
        assert result is not None
        assert any(kw in result for kw in ["冷", "热可可", "被窝", "多穿"])

    def test_normal_weather_no_reaction(self):
        """晴天22度应触发晴天反应（非极端天气）"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        result = get_weather_behavior("晴", "22", trigger_chance=1.0)
        # 晴天22度应匹配 sunny 触发器
        assert result is not None
        assert len(result) > 5  # 应该有合理的回复内容

    def test_probability_gating(self):
        """概率为0时不应触发"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        result = get_weather_behavior("暴雨", "10", trigger_chance=0.0)
        assert result is None

    def test_empty_condition_returns_none(self):
        """空天气状况返回 None"""
        from plugins.deepseek.behavior_engine import get_weather_behavior
        result = get_weather_behavior("", "20", trigger_chance=1.0)
        assert result is None


# ============================================================
# 季节性愿望测试
# ============================================================

class TestSeasonalWish:
    def test_seasonal_wish_returns_string(self):
        """100%概率时应返回季节愿望"""
        from plugins.deepseek.behavior_engine import get_seasonal_wish
        # 多试几次避免极小概率的随机失败
        for _ in range(5):
            result = get_seasonal_wish(trigger_chance=1.0)
            if result is not None:
                assert len(result) >= 4
                return
        pytest.fail("get_seasonal_wish returned None even with trigger_chance=1.0")

    def test_seasonal_wish_probability(self):
        """0%概率时不应触发"""
        from plugins.deepseek.behavior_engine import get_seasonal_wish
        result = get_seasonal_wish(trigger_chance=0.0)
        assert result is None

    def test_seasonal_wish_content(self):
        """愿望内容应包含季节特征"""
        from plugins.deepseek.behavior_engine import get_seasonal_wish
        month = datetime.now().month
        result = get_seasonal_wish(trigger_chance=1.0)
        assert result is not None
        if 3 <= month <= 5:
            assert any(kw in result for kw in ["花", "春", "风筝", "散步", "困"])
        elif 6 <= month <= 8:
            assert any(kw in result for kw in ["热", "冰", "游泳", "海边", "西瓜", "空调"])
        elif 9 <= month <= 11:
            assert any(kw in result for kw in ["秋", "奶茶", "散步", "枫叶", "舒服"])
        else:
            assert any(kw in result for kw in ["冷", "被窝", "热可可", "雪人", "火锅"])


# ============================================================
# 随机行为测试
# ============================================================

class TestRandomBehavior:
    def test_random_behavior_returns_dict(self):
        """100%概率时应返回行为字典"""
        from plugins.deepseek.behavior_engine import get_random_behavior
        result = get_random_behavior(trigger_chance=1.0)
        assert result is not None
        assert "type" in result
        assert "text" in result

    def test_random_behavior_probability(self):
        """0%概率时不应触发"""
        from plugins.deepseek.behavior_engine import get_random_behavior
        result = get_random_behavior(trigger_chance=0.0)
        assert result is None

    def test_behavior_types_valid(self):
        """行为类型应该是有效值"""
        from plugins.deepseek.behavior_engine import get_random_behavior
        valid_types = ["sudden_thought", "mood_share", "anticipation", "curiosity", "promise", "tease"]
        for _ in range(20):
            result = get_random_behavior(trigger_chance=1.0)
            if result:
                assert result["type"] in valid_types

    def test_sleeping_reduces_active_behaviors(self):
        """深夜应减少活跃类行为"""
        from plugins.deepseek.behavior_engine import get_random_behavior
        active_count = 0
        for _ in range(100):
            result = get_random_behavior(schedule_period="sleeping", trigger_chance=1.0)
            if result and result["type"] in ("anticipation", "curiosity"):
                active_count += 1
        # 深夜活跃类行为应该较少
        assert active_count < 50  # 远小于正常比例


# ============================================================
# 活跃度波动测试
# ============================================================

class TestVerbosityModifier:
    def test_sleeping_reduces_verbosity(self):
        """深夜应降低活跃度"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        mod = get_verbosity_modifier(schedule_period="sleeping", hour=3)
        assert mod < 0.7

    def test_active_increases_verbosity(self):
        """活跃时段应提高活跃度"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        mod = get_verbosity_modifier(schedule_period="active", hour=15)
        assert mod >= 0.8

    def test_angry_reduces_verbosity(self):
        """生气应降低活跃度"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        mod = get_verbosity_modifier(bot_mood_dominant="生气")
        assert mod < 0.8

    def test_happy_increases_verbosity(self):
        """开心应提高活跃度"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        mod = get_verbosity_modifier(bot_mood_dominant="开心")
        assert mod > 1.0

    def test_weekend_bonus(self):
        """周末应有额外活跃度"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        weekday_mod = get_verbosity_modifier(is_weekend=False)
        weekend_mod = get_verbosity_modifier(is_weekend=True)
        assert weekend_mod >= weekday_mod

    def test_modifier_in_range(self):
        """修正系数应在 0.4~1.5 范围内"""
        from plugins.deepseek.behavior_engine import get_verbosity_modifier
        for _ in range(50):
            mod = get_verbosity_modifier()
            assert 0.4 <= mod <= 1.5


# ============================================================
# 综合提示生成测试
# ============================================================

class TestBehaviorHint:
    def test_hint_with_weather(self):
        """有天气时应优先返回天气提示"""
        from plugins.deepseek.behavior_engine import get_behavior_hint
        with patch('plugins.deepseek.behavior_engine.get_weather_behavior', return_value="外面下雨了好冷"):
            hint = get_behavior_hint("暴雨", "10")
            assert hint is not None
            assert "雨" in hint or "天气" in hint

    def test_hint_returns_none_sometimes(self):
        """不应每次都返回提示"""
        from plugins.deepseek.behavior_engine import get_behavior_hint
        none_count = 0
        for _ in range(50):
            result = get_behavior_hint("晴", "22")
            if result is None:
                none_count += 1
        assert none_count > 0  # 应该有不触发的情况


# ============================================================
# prompt 注入测试
# ============================================================

class TestPromptInjection:
    def test_behavior_hint_in_prompt(self):
        """prompt.py 应该有 behavior_hint 参数"""
        with open(os.path.join(os.path.dirname(__file__), '..', 'plugins', 'deepseek', 'prompt.py'), 'r', encoding='utf-8') as f:
            content = f.read()
        assert 'behavior_hint' in content
        assert '行为模式' in content
