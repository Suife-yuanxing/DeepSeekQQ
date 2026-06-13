# -*- coding: utf-8 -*-
"""火山引擎 TTS 测试 — mock API 调用验证逻辑。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]


class TestVolcanoEmotionVoiceMap:
    """测试情绪→音色映射。"""

    def test_known_emotions_have_voice(self):
        from plugins.deepseek.voice_volcano import EMOTION_VOICE_MAP
        known = ["开心", "难过", "生气", "撒娇", "担心", "期待", "感动", "嫌弃", "害羞", "傲娇", "爱"]
        for emo in known:
            assert emo in EMOTION_VOICE_MAP, f"情绪 '{emo}' 应有音色映射"
            voice = EMOTION_VOICE_MAP[emo]
            assert voice, f"情绪 '{emo}' 的音色不应为空"
            assert "_streaming" in voice or "bigtts" in voice, f"音色 '{voice}' 应为有效的火山引擎音色"

    def test_different_emotions_have_different_voices(self):
        """验证不同情绪使用不同音色（非全部相同）。"""
        from plugins.deepseek.voice_volcano import EMOTION_VOICE_MAP
        # 排除唱歌模式（它用特殊音色）
        voices = {k: v for k, v in EMOTION_VOICE_MAP.items() if k != "singing"}
        unique_voices = set(voices.values())
        assert len(unique_voices) > 1, f"应该有多种音色表达不同情绪，实际只有 {len(unique_voices)} 种"

    def test_singing_uses_bigtts(self):
        """唱歌模式保持使用大模型娇喘女声。"""
        from plugins.deepseek.voice_volcano import EMOTION_VOICE_MAP
        assert EMOTION_VOICE_MAP["singing"] == "zh_female_jiaochuannv_uranus_bigtts"


class TestGenerateVolcanoVoice:
    """测试 generate_volcano_voice 函数逻辑。"""

    @pytest.mark.asyncio
    async def test_no_credentials_returns_none(self):
        from plugins.deepseek.voice_volcano import generate_volcano_voice

        # config 中没有凭据时应返回 None
        with patch("plugins.deepseek.voice_volcano.VOLCANO_APP_ID", ""), \
             patch("plugins.deepseek.voice_volcano.VOLCANO_ACCESS_TOKEN", ""):
            result = await generate_volcano_voice("测试文本")
            assert result is None

    def test_voice_type_override(self):
        from plugins.deepseek.voice_volcano import EMOTION_VOICE_MAP
        # 不同情绪映射到不同 BV 系列音色
        assert EMOTION_VOICE_MAP["开心"] != EMOTION_VOICE_MAP["难过"], "开心和难过的音色应该不同"
        assert EMOTION_VOICE_MAP["撒娇"] != EMOTION_VOICE_MAP["生气"], "撒娇和生气的音色应该不同"

    def test_default_voice(self):
        from plugins.deepseek.voice_volcano import DEFAULT_VOICE
        assert DEFAULT_VOICE == "BV001_streaming"
