"""测试跨会话 bot 情绪记忆功能。"""
import json
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from plugins.deepseek.memory import _build_bot_emotion_memory_hint, _format_time_ago
pytestmark = [pytest.mark.unit]



class TestFormatTimeAgo:
    """时间格式化测试。"""

    def test_minutes(self):
        assert _format_time_ago(0.5) == "刚才"

    def test_hours(self):
        assert _format_time_ago(3) == "3小时前"

    def test_yesterday(self):
        assert _format_time_ago(20) == "昨天"

    def test_day_before_yesterday(self):
        assert _format_time_ago(40) == "前天"

    def test_days(self):
        assert _format_time_ago(100) == "4天前"


class TestBuildBotEmotionMemoryHint:
    """_build_bot_emotion_memory_hint 单元测试。"""

    def _make_state(self, mood_data=None):
        """构造 session_state dict。"""
        data = {}
        if mood_data:
            data["mood"] = mood_data
        return {"bot_mood_snapshot": json.dumps(data, ensure_ascii=False)}

    def test_empty_snapshot_returns_none(self):
        """空快照返回 None。"""
        state = {"bot_mood_snapshot": "{}"}
        assert _build_bot_emotion_memory_hint(state, 2) is None

    def test_no_snapshot_returns_none(self):
        """无快照字段返回 None。"""
        state = {}
        assert _build_bot_emotion_memory_hint(state, 2) is None

    def test_calm_emotion_returns_none(self):
        """平静情绪不触发回忆。"""
        state = self._make_state({
            "valence": 0, "arousal": 0.2, "dominant": "平静",
            "reason": "", "time": time.time()
        })
        assert _build_bot_emotion_memory_hint(state, 2) is None

    def test_anger_within_hours(self):
        """几小时前的生气应触发回忆。"""
        state = self._make_state({
            "valence": -0.5, "arousal": 0.7, "dominant": "生气",
            "reason": "他说了过分的话", "time": time.time() - 3600 * 3
        })
        # 用 random.seed 确保触发
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.1):
            result = _build_bot_emotion_memory_hint(state, 3)
        assert result is not None
        assert "生气" in result or "脾气" in result

    def test_anger_with_reason(self):
        """有原因的生气应在 hint 中包含原因。"""
        state = self._make_state({
            "valence": -0.5, "arousal": 0.7, "dominant": "生气",
            "reason": "他说了过分的话", "time": time.time()
        })
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.1):
            result = _build_bot_emotion_memory_hint(state, 1)
        assert result is not None
        assert "过分的话" in result

    def test_happiness_hint(self):
        """开心情绪应返回正面 hint。"""
        state = self._make_state({
            "valence": 0.6, "arousal": 0.5, "dominant": "开心",
            "reason": "", "time": time.time()
        })
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.1):
            result = _build_bot_emotion_memory_hint(state, 2)
        assert result is not None
        assert "开心" in result or "心情" in result

    def test_jealousy_hint(self):
        """吃醋情绪应返回相关 hint。"""
        state = self._make_state({
            "valence": -0.3, "arousal": 0.5, "dominant": "吃醋",
            "reason": "他提到了别的女生", "time": time.time()
        })
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.1):
            result = _build_bot_emotion_memory_hint(state, 2)
        assert result is not None
        assert "醋" in result

    def test_shy_hint(self):
        """害羞情绪应返回相关 hint。"""
        state = self._make_state({
            "valence": 0.3, "arousal": 0.4, "dominant": "害羞",
            "reason": "", "time": time.time()
        })
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.1):
            result = _build_bot_emotion_memory_hint(state, 2)
        assert result is not None
        assert "害羞" in result

    def test_old_emotion_lower_chance(self):
        """很久以前的情绪触发概率更低。"""
        state = self._make_state({
            "valence": -0.5, "arousal": 0.7, "dominant": "生气",
            "reason": "", "time": time.time() - 3600 * 100
        })
        # 72-168h: 10% chance, random=0.5 不触发
        with patch("plugins.deepseek.memory_cache.random.random", return_value=0.5):
            result = _build_bot_emotion_memory_hint(state, 100)
        assert result is None

    def test_very_old_emotion_no_recall(self):
        """超过7天的情绪不回忆。"""
        state = self._make_state({
            "valence": -0.5, "arousal": 0.7, "dominant": "生气",
            "reason": "", "time": time.time() - 3600 * 200
        })
        assert _build_bot_emotion_memory_hint(state, 200) is None

    def test_invalid_json_returns_none(self):
        """无效 JSON 不崩溃。"""
        state = {"bot_mood_snapshot": "not json"}
        assert _build_bot_emotion_memory_hint(state, 2) is None

    def test_no_mood_key_returns_none(self):
        """无 mood 键返回 None。"""
        state = {"bot_mood_snapshot": json.dumps({"farewell_time": 123})}
        assert _build_bot_emotion_memory_hint(state, 2) is None


class TestPromptInjection:
    """测试 prompt 注入。"""

    def test_emotion_memory_hint_in_prompt(self):
        """bot_emotion_memory_hint 应注入到 system prompt 中。"""
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="你还在吗",
            bot_emotion_memory_hint="你昨天有点生气，因为他说了过分的话。如果他态度好了可以傲娇地消气。",
        )
        assert "情绪回忆" in prompt
        assert "生气" in prompt

    def test_no_emotion_memory_when_empty(self):
        """无 hint 时不注入。"""
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="你好",
            bot_emotion_memory_hint=None,
        )
        assert "情绪回忆" not in prompt
