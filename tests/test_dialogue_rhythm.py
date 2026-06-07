"""对话节奏优化测试 — 话题桥接、连发拆分、破冰内容、换话题过渡。"""
import pytest
from unittest.mock import AsyncMock, patch


# ============================================================
# 话题桥接测试
# ============================================================

class TestTopicBridge:
    def test_no_bridge_for_small_shift(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_bridge
        result = get_topic_bridge("游戏", "游戏好玩吗", 0.2)
        assert result == ""

    def test_light_bridge(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_bridge, _LIGHT_BRIDGES
        result = get_topic_bridge("游戏", "游戏攻略", 0.4)
        assert result != ""
        # 结果应以某个轻度桥接短语开头
        assert any(result.startswith(b.strip()) for b in _LIGHT_BRIDGES)

    def test_medium_bridge(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_bridge
        result = get_topic_bridge("游戏", "今天吃什么", 0.7)
        assert result != ""

    def test_heavy_bridge_mentions_prev_topic(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_bridge
        # 重度转移有 50% 概率提及旧话题，多测几次
        mentioned = False
        for _ in range(20):
            result = get_topic_bridge("游戏", "量子力学", 0.9)
            if "游戏" in result:
                mentioned = True
                break
        # 至少有一次应该提及旧话题
        assert mentioned or "怎么突然" in result or "等下" in result


# ============================================================
# 连发拆分测试
# ============================================================

class TestBurstSplit:
    def test_short_text_no_split(self):
        from plugins.deepseek.dialogue_rhythm import should_split_to_bursts
        result = should_split_to_bursts("哈哈")
        assert result == []

    def test_negative_emotion_no_split(self):
        from plugins.deepseek.dialogue_rhythm import should_split_to_bursts
        # 负面情绪不连发
        for _ in range(20):
            result = should_split_to_bursts(
                "我觉得这个事情需要认真考虑一下，不能太着急",
                emotion_valence=-0.5
            )
            if result:
                break
        assert result == []

    def test_split_semantic_sentences(self):
        from plugins.deepseek.dialogue_rhythm import _split_reply_semantically
        result = _split_reply_semantically("你好呀！今天天气真好。我们出去玩吧。")
        assert len(result) >= 2

    def test_split_newlines(self):
        from plugins.deepseek.dialogue_rhythm import _split_reply_semantically
        result = _split_reply_semantically("第一行\n第二行\n第三行")
        assert len(result) >= 2

    def test_split_short_returns_empty(self):
        from plugins.deepseek.dialogue_rhythm import _split_reply_semantically
        result = _split_reply_semantically("嗯")
        assert result == []


# ============================================================
# 破冰内容测试
# ============================================================

class TestIcebreaker:
    @pytest.mark.asyncio
    async def test_icebreaker_with_session_recovery(self):
        from plugins.deepseek.dialogue_rhythm import get_icebreaker_context
        recovery = {
            "last_topic": "原神",
            "time_hint": "昨天",
        }
        # 70% 概率基于上下文
        found_context = False
        for _ in range(20):
            result = await get_icebreaker_context(recovery)
            if result and "原神" in result:
                found_context = True
                break
        assert found_context

    @pytest.mark.asyncio
    async def test_icebreaker_without_recovery(self):
        from plugins.deepseek.dialogue_rhythm import get_icebreaker_context
        result = await get_icebreaker_context({})
        # 可能返回 None（概率性）
        # 但不应报错
        assert result is None or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_icebreaker_with_bot_mood(self):
        from plugins.deepseek.dialogue_rhythm import get_icebreaker_context
        mood = {"dominant": "开心", "reason": "被夸奖了"}
        found_mood = False
        for _ in range(20):
            result = await get_icebreaker_context({}, bot_mood=mood)
            if result and "开心" in result:
                found_mood = True
                break
        assert found_mood


# ============================================================
# 换话题过渡测试
# ============================================================

class TestTopicTransition:
    def test_no_transition_for_small_shift(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_transition_hint
        result = get_topic_transition_hint("游戏", "游戏好玩吗", 0.3, "闲聊")
        assert result == ""

    def test_sharing_intent(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_transition_hint
        result = get_topic_transition_hint("游戏", "美食", 0.7, "分享")
        assert "直接跟" in result

    def test_question_intent(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_transition_hint
        result = get_topic_transition_hint("游戏", "美食", 0.7, "提问")
        assert "直接回答" in result

    def test_heavy_shift_hint(self):
        from plugins.deepseek.dialogue_rhythm import get_topic_transition_hint
        result = get_topic_transition_hint("游戏", "量子力学", 0.9, "闲聊")
        assert "游戏" in result or "量子力学" in result


# ============================================================
# 反应词前缀测试
# ============================================================

class TestReactionPrefix:
    def test_reaction_prefix_positive(self):
        from plugins.deepseek.handler_humanize import maybe_add_reaction_prefix
        # 多测几次因为是概率性的
        found = False
        for _ in range(50):
            result = maybe_add_reaction_prefix("你说得对呀", 0.5)
            if result != "你说得对呀":
                found = True
                assert any(kw in result for kw in ["诶", "哦", "嗯", "噢"])
                break
        # 10% 概率，50次应该至少触发一次
        assert found

    def test_reaction_prefix_short_text_unchanged(self):
        from plugins.deepseek.handler_humanize import maybe_add_reaction_prefix
        result = maybe_add_reaction_prefix("嗯", 0.5)
        assert result == "嗯"


# ============================================================
# 连发延迟测试
# ============================================================

class TestBurstDelay:
    def test_burst_delays_realistic(self):
        """首条延迟完整（阅读+思考+打字），后续 2~5 秒 burst 延迟。"""
        from plugins.deepseek.utils import calc_burst_delays
        parts = ["第一条消息", "第二条消息", "第三条消息"]
        # 多次取平均消除随机性
        totals = [0.0, 0.0, 0.0]
        runs = 20
        for _ in range(runs):
            delays = calc_burst_delays(parts)
            assert len(delays) == 3
            for j in range(3):
                totals[j] += delays[j]
        avg = [t / runs for t in totals]
        # 首条延迟应明显大于后续（首条有阅读+思考时间）
        assert avg[0] > avg[1]
        # 后续 burst 延迟在 2~5 秒范围
        assert 2.0 <= avg[1] <= 5.0
        assert 2.0 <= avg[2] <= 5.0

    def test_burst_delays_empty(self):
        from plugins.deepseek.utils import calc_burst_delays
        assert calc_burst_delays([]) == []

    def test_single_message_normal_delay(self):
        from plugins.deepseek.utils import calc_burst_delays
        delays = calc_burst_delays(["一条消息"])
        assert len(delays) == 1
        assert 0.5 <= delays[0] <= 15.0


# ============================================================
# 节奏规则文本测试
# ============================================================

class TestRhythmRules:
    def test_rhythm_rules_not_empty(self):
        from plugins.deepseek.dialogue_rhythm import RHYTHM_RULES
        assert len(RHYTHM_RULES) > 50
        assert "真人" in RHYTHM_RULES or "QQ" in RHYTHM_RULES
