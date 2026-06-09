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
            assert EMOTION_VOICE_MAP[emo].endswith("_streaming"), f"音色 '{EMOTION_VOICE_MAP[emo]}' 应以 _streaming 结尾"

    def test_all_voices_are_valid(self):
        from plugins.deepseek.voice_volcano import EMOTION_VOICE_MAP
        valid_voices = {
            "BV001_streaming", "BV002_streaming", "BV004_streaming",
            "BV405_streaming", "BV406_streaming", "BV407_streaming",
            "BV408_streaming", "BV700_streaming", "BV701_streaming",
        }
        for emo, voice in EMOTION_VOICE_MAP.items():
            assert voice in valid_voices, f"'{emo}' 的音色 '{voice}' 不是有效的 BV*_streaming 音色"


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
        # 验证覆盖逻辑：开心 → BV405_streaming
        assert EMOTION_VOICE_MAP["开心"] == "BV405_streaming"
        # 撒娇 → BV407_streaming
        assert EMOTION_VOICE_MAP["撒娇"] == "BV407_streaming"

    def test_default_voice(self):
        from plugins.deepseek.voice_volcano import DEFAULT_VOICE
        assert DEFAULT_VOICE == "BV002_streaming"
