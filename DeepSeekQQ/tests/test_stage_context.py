"""Test stage_context — 上下文分析阶段。

C-4: 覆盖上下文分析阶段的核心行为。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_ctx(**overrides) -> MagicMock:
    ctx = MagicMock()
    ctx.raw_msg = overrides.pop("raw_msg", "你好呀今天心情怎么样")
    ctx.user_id = overrides.pop("user_id", "12345")
    ctx.session_id = overrides.pop("session_id", "sess_001")
    ctx.is_group = overrides.pop("is_group", False)
    ctx.has_share = overrides.pop("has_share", False)
    ctx.image_path = overrides.pop("image_path", "")
    ctx.voice_features = overrides.pop("voice_features", None)
    ctx.complexity = overrides.pop("complexity", "normal")
    ctx.affection = overrides.pop("affection", {"score": 100})
    ctx.scenes = overrides.pop("scenes", [])
    ctx.contagion_result = overrides.pop("contagion_result", None)
    ctx.analysis = None
    ctx.search_result = None
    ctx.world_context = ""
    ctx.reminder_context = ""
    ctx.emotion_params = {}
    ctx.bot_mood_result = None
    ctx.user_prefs = {}
    ctx.recent_memories = []
    ctx.relevant_tags = []
    ctx.mood = {}
    ctx.session_recovery = {}
    ctx.schedule = None

    for hint in ("emotion_memory_hint", "shared_memory_hint", "private_meme_hint",
                 "date_hint", "milestone_hint", "affection_decay_hint",
                 "disclosure_hint", "emotion_recovery_hint", "activity_hint",
                 "topic_bridge", "topic_transition", "icebreaker_hint",
                 "behavior_hint", "group_heat_description", "scroll_hint",
                 "group_social_hint", "group_meme_hint", "group_role_hint",
                 "nickname_hint", "interest_hint", "growth_hint",
                 "catchphrase_hint", "user_profile_summary",
                 "personality_drift_hints", "value_hints",
                 "past_opinions_hint", "reply_gap_hint", "fatigue_hint"):
        setattr(ctx, hint, overrides.pop(hint, None))

    ctx.is_first_today = overrides.pop("is_first_today", False)
    ctx.should_inject_feed = overrides.pop("should_inject_feed", False)
    ctx.fatigue_level = 0
    ctx.past_opinions = None
    ctx._weather_info = None
    ctx.bot = overrides.pop("bot", MagicMock())
    ctx.bot.send = AsyncMock()
    ctx.event = overrides.pop("event", MagicMock())
    ctx.event.user_id = ctx.user_id
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestSimpleMessageFastPath:
    """简单消息跳过深度分析。"""

    async def test_simple_message_skips_full_analysis(self):
        with patch("plugins.deepseek.stages.stage_context.save_and_get_context_with_history",
                   AsyncMock(return_value=([], [], {"score": 50}, {"score": 50}, []))):
            with patch("plugins.deepseek.stages.stage_context._run_full_analysis") as mock_full:
                from plugins.deepseek.stages.stage_context import _stage_context
                ctx = _make_ctx(complexity="simple", raw_msg="嗯")
                result = await _stage_context(ctx)
                assert result is None
                mock_full.assert_not_called()

    async def test_simple_message_sets_defaults(self):
        with patch("plugins.deepseek.stages.stage_context.save_and_get_context_with_history",
                   AsyncMock(return_value=([], [], {"score": 50}, {"score": 50}, []))):
            with patch("plugins.deepseek.stages.stage_context._run_full_analysis"):
                from plugins.deepseek.stages.stage_context import _stage_context
                ctx = _make_ctx(complexity="simple", raw_msg="好")
                await _stage_context(ctx)
                assert ctx.analysis is not None
                assert ctx.world_context == ""
                assert ctx.search_result is None


class TestCoreAnalysisErrorHandling:
    """核心分析函数的错误容错。"""

    async def test_core_analysis_handles_search_error(self):
        with patch("plugins.deepseek.stages.stage_context.search",
                   AsyncMock(side_effect=RuntimeError("down"))):
            from plugins.deepseek.stages.stage_context import _run_core_analysis
            ctx = _make_ctx()
            await _run_core_analysis(ctx, [])
            assert ctx.search_result is None

    async def test_core_analysis_handles_weather_error(self):
        with patch("plugins.deepseek.stages.stage_context.build_world_context_prompt",
                   AsyncMock(side_effect=RuntimeError("down"))):
            from plugins.deepseek.stages.stage_context import _run_core_analysis
            ctx = _make_ctx()
            await _run_core_analysis(ctx, [])
            assert ctx.world_context == ""

    async def test_core_analysis_sets_analysis(self):
        from plugins.deepseek.stages.stage_context import _run_core_analysis
        ctx = _make_ctx()
        await _run_core_analysis(ctx, [])
        assert ctx.analysis is not None


class TestBatch1Queries:
    """批量查询 1：情绪更新、偏好提示、记忆提示。"""

    async def test_batch1_sets_bot_mood(self):
        from plugins.deepseek.stages.stage_context import _run_batch1_queries
        ctx = _make_ctx()
        ctx.analysis = MagicMock()
        ctx.analysis.emotion = MagicMock()
        ctx.analysis.emotion.dominant = "平静"
        await _run_batch1_queries(ctx)
        assert "dominant" in ctx.bot_mood_result


class TestSyncComputations:
    """同步计算：schedule、activity、scenes。"""

    def test_sync_computations_sets_schedule(self):
        from plugins.deepseek.stages.stage_context import _run_sync_computations
        ctx = _make_ctx()
        ctx.analysis = MagicMock()
        ctx.analysis.emotion = MagicMock()
        ctx.analysis.emotion.dominant = "平静"
        ctx.analysis.emotion.confidence = 0.3
        ctx.analysis.context = MagicMock()
        ctx.analysis.context.topic_shift_score = 0
        ctx.analysis.context.topic_summary = ""
        ctx.analysis.context.user_intent = "闲聊"
        _run_sync_computations(ctx)
        assert ctx.schedule is not None

    def test_sync_computations_sets_scenes(self):
        from plugins.deepseek.stages.stage_context import _run_sync_computations
        ctx = _make_ctx()
        ctx.analysis = MagicMock()
        ctx.analysis.emotion = MagicMock()
        ctx.analysis.emotion.dominant = "平静"
        ctx.analysis.emotion.confidence = 0.3
        ctx.analysis.context = MagicMock()
        ctx.analysis.context.topic_shift_score = 0
        ctx.analysis.context.topic_summary = ""
        ctx.analysis.context.user_intent = "闲聊"
        _run_sync_computations(ctx)
        assert isinstance(ctx.scenes, list)


class TestPersonalityDrift:
    """人设演化与价值分析。"""

    async def test_personality_drift_not_crash(self):
        from plugins.deepseek.stages.stage_context import _run_personality_drift
        ctx = _make_ctx()
        await _run_personality_drift(ctx)

    async def test_value_analysis_not_crash(self):
        from plugins.deepseek.stages.stage_context import _run_value_analysis
        ctx = _make_ctx()
        await _run_value_analysis(ctx)


class TestFatigueAndGap:
    """对话疲劳感知。"""

    async def test_fatigue_not_crash(self):
        with patch("plugins.deepseek.db_memories.get_last_bot_reply_time",
                   AsyncMock(return_value=0)):
            from plugins.deepseek.stages.stage_context import _run_fatigue_and_gap
            ctx = _make_ctx()
            await _run_fatigue_and_gap(ctx, "平静")
            assert ctx.fatigue_level is not None
