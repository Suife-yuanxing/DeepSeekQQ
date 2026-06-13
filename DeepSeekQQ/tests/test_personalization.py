"""个性化深化测试 — 专属昵称、共同兴趣、成长叙事、口头禅。"""
import os
import pytest
import time
from unittest.mock import AsyncMock, patch
pytestmark = [pytest.mark.unit]



# ============================================================
# 专属昵称测试
# ============================================================

class TestNickname:
    def test_custom_nickname_priority(self):
        """自定义昵称优先"""
        from plugins.deepseek.personalization import generate_nickname
        result = generate_nickname(affection_score=500, custom_nickname="小甜甜")
        assert result == "小甜甜"

    def test_high_affection_nickname(self):
        """高好感度应有亲密昵称"""
        from plugins.deepseek.personalization import generate_nickname
        result = generate_nickname(affection_score=500)
        # _NICKNAME_TIERS[500] = ["亲爱的", "宝贝", "心肝", "笨蛋"]
        assert result in ["亲爱的", "宝贝", "心肝", "笨蛋"]

    def test_low_affection_nickname(self):
        """低好感度应默认用'你'"""
        from plugins.deepseek.personalization import generate_nickname
        result = generate_nickname(affection_score=10)
        assert result == "你"

    def test_tsundere_style_nickname(self):
        """傲娇风格应有专属昵称"""
        from plugins.deepseek.personalization import generate_nickname
        result = generate_nickname(affection_score=200, relationship_style="tsundere")
        assert result in ["笨蛋", "傻瓜"]

    def test_gentle_style_nickname(self):
        """温柔风格应有专属昵称"""
        from plugins.deepseek.personalization import generate_nickname
        result = generate_nickname(affection_score=200, relationship_style="gentle")
        assert result in ["乖乖", "小可爱"]

    def test_nickname_hint_returns_none_for_low(self):
        """低好感度不应返回昵称提示"""
        from plugins.deepseek.personalization import get_nickname_hint
        result = get_nickname_hint(10, "")
        assert result is None

    def test_nickname_hint_returns_string_for_high(self):
        """高好感度应返回昵称提示"""
        from plugins.deepseek.personalization import get_nickname_hint
        result = get_nickname_hint(200, "")
        assert result is not None
        assert "称呼" in result


# ============================================================
# 共同兴趣测试
# ============================================================

class TestSharedInterests:
    @pytest.mark.asyncio
    async def test_shared_interest_found(self):
        """有共同兴趣时应返回提示"""
        from plugins.deepseek.personalization import discover_shared_interests
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock,
                   return_value={"topic_interest": {"游戏": 0.8, "音乐": 0.5}}):
            result = await discover_shared_interests("test_user")
            assert result is not None
            assert "游戏" in result or "音乐" in result

    @pytest.mark.asyncio
    async def test_no_shared_interest(self):
        """无共同兴趣时返回 None"""
        from plugins.deepseek.personalization import discover_shared_interests
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock,
                   return_value={"topic_interest": {"编程": 0.8, "数学": 0.5}}):
            result = await discover_shared_interests("test_user")
            assert result is None

    @pytest.mark.asyncio
    async def test_no_interests_returns_none(self):
        """无兴趣数据时返回 None"""
        from plugins.deepseek.personalization import discover_shared_interests
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock,
                   return_value={}):
            result = await discover_shared_interests("test_user")
            assert result is None


# ============================================================
# 成长叙事测试
# ============================================================

class TestGrowthNarrative:
    def test_narrative_with_full_data(self):
        """有完整数据时应生成叙事"""
        from plugins.deepseek.personalization import get_growth_narrative
        first = time.time() - 100 * 86400  # 100天前
        result = get_growth_narrative(250, 1500, 30, first)
        assert result is not None
        assert "100天" in result or "1500" in result or "30天" in result

    def test_narrative_minimal_data(self):
        """最少数据时也应返回"""
        from plugins.deepseek.personalization import get_growth_narrative
        result = get_growth_narrative(50, 10, 0, 0)
        # 可能返回 None（数据太少）
        assert result is None or isinstance(result, str)

    def test_narrative_high_score(self):
        """高好感度应有亲密描述"""
        from plugins.deepseek.personalization import get_growth_narrative
        first = time.time() - 200 * 86400
        result = get_growth_narrative(600, 3000, 60, first)
        assert result is not None
        assert "命定之人" in result or "重要" in result



# ============================================================
# 个性化口头禅测试
# ============================================================

class TestPersonalizedCatchphrase:
    def test_tsundere_catchphrase(self):
        """傲娇风格应有傲娇口头禅"""
        from plugins.deepseek.personalization import get_personalized_catchphrase
        found = False
        for _ in range(50):
            result = get_personalized_catchphrase("neutral", "tsundere", 200)
            if result in ("哼", "切", "笨蛋"):
                found = True
                break
        assert found

    def test_high_affection_more_catchphrases(self):
        """高好感度应有更多口头禅"""
        from plugins.deepseek.personalization import get_personalized_catchphrase
        high_count = 0
        low_count = 0
        for _ in range(100):
            if get_personalized_catchphrase("neutral", "gentle", 500):
                high_count += 1
            if get_personalized_catchphrase("neutral", "gentle", 20):
                low_count += 1
        assert high_count >= low_count

    def test_angry_reduces_catchphrases(self):
        """生气时口头禅应减少"""
        from plugins.deepseek.personalization import get_personalized_catchphrase
        happy_count = 0
        angry_count = 0
        for _ in range(100):
            if get_personalized_catchphrase("开心", "gentle", 200):
                happy_count += 1
            if get_personalized_catchphrase("生气", "gentle", 200):
                angry_count += 1
        assert happy_count >= angry_count


# ============================================================
# 综合提示测试
# ============================================================

class TestPersonalizationHints:
    @pytest.mark.asyncio
    async def test_hints_returns_dict(self):
        """应返回包含所有提示的字典"""
        from plugins.deepseek.personalization import get_personalization_hints
        with patch('plugins.deepseek.db_preferences.get_user_preferences', new_callable=AsyncMock,
                   return_value={"topic_interest": {"游戏": 0.8}}):
            result = await get_personalization_hints("test_user", 200, "gentle")
            assert "nickname_hint" in result
            assert "interest_hint" in result
            assert "growth_hint" in result
            assert "catchphrase_hint" in result


# ============================================================
# prompt 注入测试
# ============================================================

class TestPromptInjection:
    def test_personalization_params_in_prompt(self):
        """prompt.py 应该有个性化参数"""
        with open(os.path.join(os.path.dirname(__file__), '..', 'plugins', 'deepseek', 'prompt.py'), 'r', encoding='utf-8') as f:
            content = f.read()
        for param in ['nickname_hint', 'interest_hint', 'growth_hint', 'catchphrase_hint']:
            assert param in content, f"prompt.py missing param: {param}"
