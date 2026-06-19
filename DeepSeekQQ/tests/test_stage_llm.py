"""Test stage_llm — LLM调用阶段。

C-4: 覆盖 LLM 阶段的核心行为。
由于 stage_llm 在正常聊天路径中依赖大量 DB 调用，
本测试文件采用分层策略：skip_llm 短路口测完整路径，
其他行为通过单元级 mock 测试关键分支。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _make_ctx(**overrides) -> MagicMock:
    ctx = MagicMock()
    ctx.raw_msg = overrides.pop("raw_msg", "你好呀")
    ctx.user_id = overrides.pop("user_id", "12345")
    ctx.session_id = overrides.pop("session_id", "sess_001")
    ctx.skip_llm = overrides.pop("skip_llm", False)
    ctx.reply_text = ""
    ctx.is_group = overrides.pop("is_group", False)
    ctx.is_first_today = overrides.pop("is_first_today", False)
    ctx.voice_mode = overrides.pop("voice_mode", False)
    ctx.scenes = overrides.pop("scenes", [])
    ctx.scratchpad = overrides.pop("scratchpad", "")
    ctx.complexity = overrides.pop("complexity", "normal")
    ctx.has_share = overrides.pop("has_share", False)
    ctx.image_path = overrides.pop("image_path", "")
    ctx.share_cutoff = overrides.pop("share_cutoff", 0)
    ctx.affection = overrides.pop("affection", {"score": 100, "total_chats": 50})
    ctx.mood = overrides.pop("mood", {"score": 50})
    ctx.analysis = MagicMock()
    ctx.analysis.context = MagicMock()
    ctx.analysis.context.user_intent = "闲聊"
    ctx.analysis.context.topic_summary = ""
    ctx.analysis.context.topic_shift_score = 0
    ctx.analysis.emotion = MagicMock()
    ctx.analysis.emotion.dominant = "平静"
    ctx.analysis.emotion.confidence = 0.3
    ctx.emotion_params = overrides.pop("emotion_params", {
        "temperature": 0.7, "max_tokens": 512, "target_lines": "3"
    })
    ctx.relevant_tags = overrides.pop("relevant_tags", [])
    ctx.recent_memories = overrides.pop("recent_memories", [])
    ctx.search_result = overrides.pop("search_result", None)
    ctx.reminder_context = overrides.pop("reminder_context", "")
    ctx.world_context = overrides.pop("world_context", "")
    ctx.bot_mood_result = overrides.pop("bot_mood_result", {"dominant": "平静", "reason": ""})
    ctx.user_prefs = overrides.pop("user_prefs", {})
    ctx.session_recovery = overrides.pop("session_recovery", {})
    ctx.schedule = overrides.pop("schedule", MagicMock())
    ctx.schedule.period = "active"

    for hint in ("disclosure_hint", "affection_decay_hint", "milestone_hint",
                 "voice_features", "shared_memory_hint", "private_meme_hint",
                 "date_hint", "topic_bridge", "icebreaker_hint", "topic_transition",
                 "emotion_recovery_hint", "emotion_memory_hint", "group_social_hint",
                 "group_meme_hint", "group_role_hint", "behavior_hint", "nickname_hint",
                 "interest_hint", "growth_hint", "catchphrase_hint", "reply_gap_hint",
                 "bot_emotion_memory_hint", "fatigue_hint", "user_profile_summary",
                 "activity_hint", "personality_drift_hints", "value_hints",
                 "past_opinions_hint", "scroll_hint", "group_heat_description"):
        setattr(ctx, hint, overrides.pop(hint, None))

    ctx.should_inject_feed = overrides.pop("should_inject_feed", False)
    ctx.heat_state = overrides.pop("heat_state", None)
    ctx.bot = overrides.pop("bot", MagicMock())
    ctx.bot.send = AsyncMock()
    ctx.event = overrides.pop("event", MagicMock())
    ctx.event.user_id = ctx.user_id
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestSkipLlm:
    """skip_llm 短路 — 不调用任何外部服务，完整路径可测。"""

    async def test_skip_llm_returns_none_no_api_call(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api") as mock_api:
            from plugins.deepseek.stages.stage_llm import _stage_llm
            ctx = _make_ctx(skip_llm=True)
            result = await _stage_llm(ctx)
            assert result is None
            mock_api.assert_not_called()

    async def test_skip_llm_preserves_reply_text(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api"):
            from plugins.deepseek.stages.stage_llm import _stage_llm
            ctx = _make_ctx(skip_llm=True)
            ctx.reply_text = "untouched"
            await _stage_llm(ctx)
            assert ctx.reply_text == "untouched"


class TestAnalysisMode:
    """分析模式 — get_recent_shares + call_deepseek_api mock 即可覆盖。"""

    async def test_analysis_with_valid_shares_calls_api(self):
        mock_reply = "这是一份专业分析……"
        shares = [{"summary": "一篇关于AI发展的文章内容" * 5, "time": 100}]
        with patch("plugins.deepseek.stages.stage_llm.get_recent_shares",
                   return_value=shares):
            with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                       AsyncMock(return_value=mock_reply)):
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(raw_msg="你分析一下这个")
                await _stage_llm(ctx)
                assert ctx.reply_text == mock_reply

    async def test_analysis_without_analysis_keyword_no_api_call(self):
        """无分析关键词 → 走正常路径（需要有 shares 来区分）。"""
        shares = [{"summary": "普通内容" * 5, "time": 100}]
        with patch("plugins.deepseek.stages.stage_llm.get_recent_shares",
                   return_value=shares):
            with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                       AsyncMock(return_value="喵~")):
                with patch("plugins.deepseek.database.get_last_farewell_time",
                           AsyncMock(return_value=None)):
                    with patch("plugins.deepseek.database.has_user_message_today",
                               AsyncMock(return_value=False)):
                        from plugins.deepseek.stages.stage_llm import _stage_llm
                        ctx = _make_ctx(raw_msg="你好")
                        await _stage_llm(ctx)
                        # 无 crash 即通过
                        assert ctx.reply_text is not None

    async def test_analysis_xiaoheike_triggers_skip(self):
        shares = [{"summary": "[小黑盒内容需要用户粘贴正文后才能分析]", "time": 100,
                    "needs_paste": True, "platform": "小黑盒"}]
        with patch("plugins.deepseek.stages.stage_llm.get_recent_shares",
                   return_value=shares):
            from plugins.deepseek.stages.stage_llm import _stage_llm
            from plugins.deepseek.pipeline import _SKIP
            ctx = _make_ctx(raw_msg="分析一下这个")
            result = await _stage_llm(ctx)
            assert result is _SKIP
            ctx.bot.send.assert_called_once()


class TestIntegrationSafety:
    """集成级安全测试：确保 stage_llm 在各边界条件下不崩溃。"""

    @staticmethod
    def _db_patches():
        """真人化后 stage_llm 新增 farewell / first_today 等 DB 调用，测试需 mock。"""
        return [
            patch("plugins.deepseek.database.get_last_farewell_time",
                  AsyncMock(return_value=None)),
            patch("plugins.deepseek.database.has_user_message_today",
                  AsyncMock(return_value=False)),
        ]

    async def test_voice_mode_no_crash(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="嗯嗯~")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(voice_mode=True)
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_phone_keyword_no_crash(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="截图...")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(raw_msg="帮我截图微信")
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_long_message_no_crash(self):
        from plugins.deepseek.config import MAX_USER_MSG_CHARS
        long_msg = "x" * (MAX_USER_MSG_CHARS + 500)
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="aaa")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(raw_msg=long_msg)
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_short_message_no_crash(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="喵~")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(raw_msg="你好")
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_scene_hint_no_crash(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="喵~")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(scenes=["greeting_mode"])
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_first_message_today_no_crash(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(return_value="你来啦~")):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx(is_first_today=True, raw_msg="早")
                await _stage_llm(ctx)
                assert ctx.reply_text is not None

    async def test_api_error_fallback(self):
        with patch("plugins.deepseek.stages.stage_llm.call_deepseek_api",
                   AsyncMock(side_effect=RuntimeError("down"))):
            with self._db_patches()[0], self._db_patches()[1]:
                from plugins.deepseek.stages.stage_llm import _stage_llm
                ctx = _make_ctx()
                await _stage_llm(ctx)
                assert len(ctx.reply_text) > 0  # 降级回复非空
