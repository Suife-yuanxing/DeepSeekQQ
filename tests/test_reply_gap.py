"""测试已读不回感知功能。"""
import time
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from plugins.deepseek.handler import build_reply_gap_hint
pytestmark = [pytest.mark.unit, pytest.mark.needs_db]



class TestBuildReplyGapHint:
    """build_reply_gap_hint 单元测试。"""

    def test_short_gap_returns_empty(self):
        """15分钟以内的间隔不触发提示。"""
        result = build_reply_gap_hint(
            gap_seconds=10 * 60,
            affection={"score": 100},
            schedule=None,
            bot_mood="平静",
        )
        assert result == ""

    def test_zero_gap_returns_empty(self):
        """无间隔不触发提示。"""
        result = build_reply_gap_hint(0, {"score": 100}, None, "平静")
        assert result == ""

    def test_no_bot_reply_returns_empty(self):
        """无历史回复不触发提示。"""
        result = build_reply_gap_hint(0, {}, None, "平静")
        assert result == ""

    def test_late_night_medium_gap_empty(self):
        """深夜3-8小时间隔不提示（用户可能在睡觉，不要打扰）。"""
        result = build_reply_gap_hint(
            gap_seconds=6 * 3600,  # 6小时
            affection={"score": 200},
            schedule=None,
            bot_mood="平静",
            current_hour=3,  # 凌晨3点
        )
        # 3-8h区间在深夜返回空（不提示也不嘲讽）
        assert result == ""

    def test_late_night_very_long_gap(self):
        """深夜8小时以上间隔应返回自然起床提示。"""
        result = build_reply_gap_hint(
            gap_seconds=10 * 3600,
            affection={"score": 200},
            schedule=None,
            bot_mood="平静",
            current_hour=5,
        )
        assert "睡醒" in result or "起床" in result

    def test_late_night_short_gap_normal(self):
        """深夜短间隔（<3小时）走正常好感度逻辑。"""
        result = build_reply_gap_hint(
            gap_seconds=2 * 3600,
            affection={"score": 200},
            schedule=None,
            bot_mood="平静",
            current_hour=2,
        )
        # gap < 3h 不触发深夜特殊逻辑，走正常高好感度路径
        assert result != ""
        # 高好感度2h gap的输出包含撒娇/委屈等关键词
        keywords = ["终于", "委屈", "生气", "小时", "没回", "没消息", "撒娇", "假装", "太久"]
        assert any(kw in result for kw in keywords), f"Expected keyword in: {result}"

    def test_high_affection_medium_gap(self):
        """高好感度用户15-60分钟间隔应有撒娇提示。"""
        result = build_reply_gap_hint(
            gap_seconds=30 * 60,
            affection={"score": 250},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert result != ""
        assert "撒娇" in result or "委屈" in result or "等" in result

    def test_high_affection_long_gap(self):
        """高好感度用户1-3小时间隔应有撒娇/委屈提示。"""
        result = build_reply_gap_hint(
            gap_seconds=2 * 3600,
            affection={"score": 250},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert result != ""
        assert "终于" in result or "委屈" in result or "生气" in result or "小时" in result

    def test_high_affection_very_long_gap(self):
        """高好感度用户3-8小时间隔应有抱怨。"""
        result = build_reply_gap_hint(
            gap_seconds=5 * 3600,
            affection={"score": 250},
            schedule=None,
            bot_mood="平静",
            current_hour=18,
        )
        assert result != ""
        assert "小时" in result or "不理" in result or "抱怨" in result

    def test_medium_affection_medium_gap(self):
        """中好感度用户15-60分钟间隔应有自然提示。"""
        result = build_reply_gap_hint(
            gap_seconds=30 * 60,
            affection={"score": 80},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert result != ""
        assert "自然" in result or "接" in result

    def test_low_affection_long_gap(self):
        """低好感度用户长间隔提示平淡。"""
        result = build_reply_gap_hint(
            gap_seconds=4 * 3600,
            affection={"score": 20},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert result != ""
        assert "简单" in result or "正常" in result or "不用" in result

    def test_low_affection_short_gap_empty(self):
        """低好感度用户15-60分钟间隔不触发提示。"""
        result = build_reply_gap_hint(
            gap_seconds=20 * 60,
            affection={"score": 20},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert result == ""

    def test_no_affection_uses_default(self):
        """无好感度数据时使用默认低好感度逻辑。"""
        result = build_reply_gap_hint(
            gap_seconds=4 * 3600,
            affection={},
            schedule=None,
            bot_mood="平静",
            current_hour=14,
        )
        assert isinstance(result, str)

    def test_returns_string_type(self):
        """始终返回字符串类型。"""
        for gap in [0, 60, 600, 3600, 7200, 28800]:
            result = build_reply_gap_hint(gap, {"score": 100}, None, "平静", current_hour=14)
            assert isinstance(result, str)


class TestReplyGapPromptInjection:
    """测试 prompt 注入。"""

    def test_prompt_includes_reply_gap(self):
        """reply_gap_hint 应注入到 system prompt 中。"""
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="你还在吗",
            reply_gap_hint="用户过了30分钟才回复，可以自然地接上话题",
        )
        assert "回复间隔" in prompt
        assert "30分钟" in prompt

    def test_prompt_no_reply_gap_when_empty(self):
        """无 reply_gap_hint 时不注入。"""
        from plugins.deepseek.prompt import build_system_prompt
        prompt = build_system_prompt(
            affection={"score": 100, "total_chats": 50, "streak_days": 3},
            mood={"dominant": "平静", "score": 50},
            length={"target_lines": 2, "style": "正常"},
            user_msg="你好",
            reply_gap_hint=None,
        )
        assert "回复间隔" not in prompt


class TestGetLastBotReplyTime:
    """测试数据库查询函数。"""

    @pytest.mark.asyncio
    async def test_returns_zero_when_no_history(self):
        """无历史记录时返回 0。"""
        from plugins.deepseek.db_memories import get_last_bot_reply_time

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"ts": None})

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_db = MagicMock()
        mock_db.execute = MagicMock(return_value=mock_ctx)

        with patch("plugins.deepseek.db_memories.get_db", new=AsyncMock(return_value=mock_db)):
            result = await get_last_bot_reply_time("test_session")
            assert result == 0

    @pytest.mark.asyncio
    async def test_returns_timestamp_when_exists(self):
        """有历史回复时返回正确时间戳。"""
        from plugins.deepseek.db_memories import get_last_bot_reply_time
        expected_ts = 1717800000.0

        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value={"ts": expected_ts})

        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)

        mock_db = MagicMock()
        mock_db.execute = MagicMock(return_value=mock_ctx)

        with patch("plugins.deepseek.db_memories.get_db", new=AsyncMock(return_value=mock_db)):
            result = await get_last_bot_reply_time("test_session")
            assert result == expected_ts
