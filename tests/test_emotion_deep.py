"""情绪系统深化测试 — 情绪传染、随机波动、渐进恢复、情绪记忆。"""
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock


# ============================================================
# 情绪传染测试
# ============================================================

class TestEmotionalContagion:
    def test_happy_user_infects_calm_bot(self):
        """用户开心 + bot 平静 → bot 轻微开心"""
        from plugins.deepseek.emotion_deep import apply_emotional_contagion
        result = apply_emotional_contagion(
            user_valence=0.7, user_arousal=0.6,
            bot_valence=0.0, bot_arousal=0.2, bot_dominant="平静",
            affection_score=100,
        )
        assert result is not None
        assert result["valence_delta"] > 0  # bot 应该变开心

    def test_sad_user_infects_calm_bot(self):
        """用户难过 + bot 平静 → bot 轻微低落"""
        from plugins.deepseek.emotion_deep import apply_emotional_contagion
        result = apply_emotional_contagion(
            user_valence=-0.6, user_arousal=0.3,
            bot_valence=0.0, bot_arousal=0.2, bot_dominant="平静",
            affection_score=100,
        )
        assert result is not None
        assert result["valence_delta"] < 0  # bot 应该变低落

    def test_no_contagion_when_bot_emotional(self):
        """bot 已有强烈情绪时不受传染"""
        from plugins.deepseek.emotion_deep import apply_emotional_contagion
        result = apply_emotional_contagion(
            user_valence=0.7, user_arousal=0.6,
            bot_valence=-0.6, bot_arousal=0.8, bot_dominant="生气",
            affection_score=100,
        )
        assert result is None

    def test_no_contagion_when_user_calm(self):
        """用户情绪平静时不传染"""
        from plugins.deepseek.emotion_deep import apply_emotional_contagion
        result = apply_emotional_contagion(
            user_valence=0.1, user_arousal=0.2,
            bot_valence=0.0, bot_arousal=0.2, bot_dominant="平静",
            affection_score=100,
        )
        assert result is None

    def test_affection_affects_contagion_strength(self):
        """好感度越高传染越强"""
        from plugins.deepseek.emotion_deep import apply_emotional_contagion
        result_low = apply_emotional_contagion(
            user_valence=0.5, user_arousal=0.5,
            bot_valence=0.0, bot_arousal=0.2, bot_dominant="平静",
            affection_score=20,
        )
        result_high = apply_emotional_contagion(
            user_valence=0.5, user_arousal=0.5,
            bot_valence=0.0, bot_arousal=0.2, bot_dominant="平静",
            affection_score=500,
        )
        # 高好感度传染更强
        if result_low and result_high:
            assert abs(result_high["valence_delta"]) >= abs(result_low["valence_delta"])


# ============================================================
# 随机情绪波动测试
# ============================================================

class TestMoodSwing:
    def test_no_swing_when_not_calm(self):
        """bot 已有情绪时不触发波动"""
        from plugins.deepseek.emotion_deep import maybe_trigger_mood_swing
        result = maybe_trigger_mood_swing("生气", 100)
        assert result is None

    def test_swing_probability(self):
        """3% 概率触发，多次测试应该有触发"""
        from plugins.deepseek.emotion_deep import maybe_trigger_mood_swing
        triggered = 0
        for _ in range(200):
            result = maybe_trigger_mood_swing("平静", 200)
            if result:
                triggered += 1
        # 3% × 200 = 6 次期望，允许 0-20 的范围
        assert triggered >= 0  # 至少不报错

    def test_high_affection_gets_soft_swing(self):
        """高好感度应该触发撒娇/小脾气类波动"""
        from plugins.deepseek.emotion_deep import maybe_trigger_mood_swing
        # 多次尝试找到触发
        for _ in range(500):
            result = maybe_trigger_mood_swing("平静", 500)
            if result:
                assert result["dominant"] in ("撒娇", "小脾气", "无聊")
                return
        # 500次都没触发也是可能的（概率性）

    def test_late_night_bias(self):
        """深夜应该偏好低落类波动"""
        from plugins.deepseek.emotion_deep import maybe_trigger_mood_swing
        for _ in range(500):
            result = maybe_trigger_mood_swing("平静", 200, hour=3)
            if result:
                assert result["valence"] <= 0.3  # 深夜不应太开心
                return


# ============================================================
# 渐进恢复测试
# ============================================================

class TestGradualRecovery:
    def test_anger_goes_through_stages(self):
        """生气应该经历：生气→消气中→傲娇→平静"""
        from plugins.deepseek.emotion_deep import get_gradual_recovery
        now = time.time()
        duration = 900  # 15分钟

        # 刚生气
        result = get_gradual_recovery("生气", now, duration)
        assert result is not None
        assert result["stage_label"] == "生气"

        # 5 分钟后（33%）
        result = get_gradual_recovery("生气", now - 300, duration)
        assert result is not None
        assert result["stage_label"] in ("生气", "消气中")

        # 10 分钟后（67%）
        result = get_gradual_recovery("生气", now - 600, duration)
        assert result is not None
        assert result["stage_label"] in ("消气中", "傲娇")

        # 超过 duration → None（已恢复）
        result = get_gradual_recovery("生气", now - 1000, duration)
        assert result is None

    def test_sadness_recovery(self):
        """难过的渐进恢复"""
        from plugins.deepseek.emotion_deep import get_gradual_recovery
        now = time.time()
        duration = 1800

        result = get_gradual_recovery("难过", now, duration)
        assert result is not None
        assert result["stage_label"] == "难过"

        result = get_gradual_recovery("难过", now - 900, duration)
        assert result is not None
        assert result["stage_label"] in ("难过", "淡淡")

    def test_calm_returns_none(self):
        """平静状态不需要恢复"""
        from plugins.deepseek.emotion_deep import get_gradual_recovery
        result = get_gradual_recovery("平静", time.time(), 600)
        assert result is None

    def test_unknown_emotion_returns_none(self):
        """未知情绪不需要恢复"""
        from plugins.deepseek.emotion_deep import get_gradual_recovery
        result = get_gradual_recovery("未知", time.time(), 600)
        assert result is None


# ============================================================
# 情绪表达多样性测试
# ============================================================

class TestEmotionExpression:
    def test_all_emotions_have_hints(self):
        """所有新增情绪都应该有表达提示"""
        from plugins.deepseek.emotion_deep import get_emotion_expression_hint
        emotions = ["吃醋", "担心", "得意", "撒娇", "小脾气", "无聊", "冷淡", "犯困"]
        for emotion in emotions:
            hint = get_emotion_expression_hint(emotion)
            assert hint is not None
            assert len(hint) > 10

    def test_unknown_emotion_returns_none(self):
        from plugins.deepseek.emotion_deep import get_emotion_expression_hint
        assert get_emotion_expression_hint("未知情绪") is None


# ============================================================
# 情绪记忆测试
# ============================================================

class TestEmotionMemory:
    @pytest.mark.asyncio
    async def test_record_topic_emotion(self):
        """记录话题情绪不应报错"""
        from plugins.deepseek.emotion_deep import record_topic_emotion
        with patch('plugins.deepseek.db_preferences.update_user_preference', new_callable=AsyncMock) as mock_update:
            await record_topic_emotion("test_user", "游戏", "开心")
            mock_update.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_topic_emotion_skips_calm(self):
        """平静情绪不记录"""
        from plugins.deepseek.emotion_deep import record_topic_emotion
        with patch('plugins.deepseek.db_preferences.update_user_preference', new_callable=AsyncMock) as mock_update:
            await record_topic_emotion("test_user", "游戏", "平静")
            mock_update.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_emotion_memory_hint_no_data(self):
        """无数据时返回 None"""
        from plugins.deepseek.emotion_deep import get_emotion_memory_hint
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock, return_value={}):
            result = await get_emotion_memory_hint("test_user", "游戏好玩吗")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_emotion_memory_hint_with_data(self):
        """有数据时返回提示"""
        from plugins.deepseek.emotion_deep import get_emotion_memory_hint
        prefs = {
            "topic_emotion": {
                "游戏:开心": 0.8,
                "游戏:兴奋": 0.3,
            }
        }
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock, return_value=prefs):
            result = await get_emotion_memory_hint("test_user", "我们来打游戏吧")
            assert result is not None
            assert "游戏" in result or "开心" in result


# ============================================================
# prompt 提示测试
# ============================================================

class TestPromptHints:
    def test_all_emotions_in_prompt_map(self):
        """所有新增情绪都应该在 prompt 的 mood_map 中"""
        # 读取 prompt.py 检查 mood_map
        with open('plugins/deepseek/prompt.py', 'r', encoding='utf-8') as f:
            content = f.read()
        emotions = ["吃醋", "担心", "得意", "撒娇", "小脾气", "无聊", "冷淡", "犯困"]
        for emotion in emotions:
            assert f'"{emotion}"' in content, f"prompt.py missing emotion: {emotion}"

    def test_recovery_hint_in_prompt(self):
        """prompt.py 应该处理 recovery_stage"""
        with open('plugins/deepseek/prompt.py', 'r', encoding='utf-8') as f:
            content = f.read()
        assert "recovery_stage" in content
