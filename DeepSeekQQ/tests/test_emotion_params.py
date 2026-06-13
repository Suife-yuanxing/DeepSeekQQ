"""功能⑤：情绪驱动渐变 — get_emotion_params 测试。"""
import pytest
from plugins.deepseek.context_analyzer import EmotionState
pytestmark = [pytest.mark.unit]



class TestGetEmotionParams:
    """测试情绪参数映射函数。"""

    def setup_method(self):
        from plugins.deepseek.handler import get_emotion_params
        self.get_params = get_emotion_params

    def test_none_emotion_returns_defaults(self):
        result = self.get_params(None)
        assert result["max_tokens"] == 1500
        assert result["temperature"] == 0.9
        assert result["sticker_chance"] == 0.25

    def test_low_confidence_returns_defaults(self):
        emotion = EmotionState(valence=0.8, arousal=0.9, confidence=0.2)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1500
        assert result["temperature"] == 0.9

    def test_excited_emotion(self):
        """V>0.5, A>0.7 → 兴奋"""
        emotion = EmotionState(valence=0.7, arousal=0.8, confidence=0.8)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1800
        assert result["temperature"] == 1.1
        assert result["sticker_chance"] == 0.50
        assert result["target_lines"] == "4-5"

    def test_happy_emotion(self):
        """V>0.3, A>0.5 → 开心"""
        emotion = EmotionState(valence=0.5, arousal=0.6, confidence=0.7)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1500
        assert result["temperature"] == 1.0
        assert result["sticker_chance"] == 0.40
        assert result["target_lines"] == "3-4"

    def test_angry_emotion(self):
        """V<-0.5, A>0.5 → 生气"""
        emotion = EmotionState(valence=-0.6, arousal=0.7, confidence=0.8)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 600
        assert result["temperature"] == 0.6
        assert result["sticker_chance"] == 0.05
        assert result["target_lines"] == "1"

    def test_sad_emotion(self):
        """V<-0.3 → 难过"""
        emotion = EmotionState(valence=-0.4, arousal=0.2, confidence=0.7)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 800
        assert result["temperature"] == 0.7
        assert result["sticker_chance"] == 0.10
        assert result["target_lines"] == "1-2"

    def test_calm_emotion(self):
        """A<0.3 → 平静"""
        emotion = EmotionState(valence=0.0, arousal=0.1, confidence=0.6)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1200
        assert result["temperature"] == 0.8
        assert result["sticker_chance"] == 0.20
        assert result["target_lines"] == "2-3"

    def test_shy_emotion(self):
        """V>0, A>0.5 → 害羞"""
        emotion = EmotionState(valence=0.2, arousal=0.6, confidence=0.7)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1000
        assert result["temperature"] == 0.9
        assert result["sticker_chance"] == 0.30
        assert result["target_lines"] == "2"

    def test_default_emotion(self):
        """默认情况"""
        emotion = EmotionState(valence=0.0, arousal=0.5, confidence=0.6)
        result = self.get_params(emotion)
        assert result["max_tokens"] == 1500
        assert result["temperature"] == 0.9
        assert result["sticker_chance"] == 0.25


class TestParseTargetLines:
    """测试 target_lines 范围解析。"""

    def setup_method(self):
        from plugins.deepseek.handler import _parse_target_lines
        self.parse = _parse_target_lines

    def test_single_number(self):
        assert self.parse("3") == 3

    def test_range(self):
        for _ in range(20):
            result = self.parse("2-4")
            assert 2 <= result <= 4

    def test_invalid_returns_default(self):
        assert self.parse("abc") == 3

    def test_empty_returns_default(self):
        assert self.parse("") == 3
