# -*- coding: utf-8 -*-
"""语音识别（STT）模块测试 — 验证代码逻辑（mock API 调用）。

真实测试需要：
1. ffmpeg 已安装
2. 百度语音凭据已配置（BAIDU_TTS_AK / BAIDU_TTS_SK）
3. 一条真实的语音文件
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
pytestmark = [pytest.mark.unit]



class TestExtractVoiceUrl:
    """测试语音 URL 提取。"""

    def test_extract_from_record_segment(self):
        from plugins.deepseek.stt import extract_voice_url
        event = MagicMock()
        seg = MagicMock()
        seg.type = "record"
        seg.data = {"url": "https://example.com/voice.amr"}
        event.get_message.return_value = [seg]
        url = extract_voice_url(event)
        assert url == "https://example.com/voice.amr"

    def test_extract_from_file_field(self):
        from plugins.deepseek.stt import extract_voice_url
        event = MagicMock()
        seg = MagicMock()
        seg.type = "record"
        seg.data = {"url": "", "file": "https://example.com/voice.amr"}
        event.get_message.return_value = [seg]
        url = extract_voice_url(event)
        assert url == "https://example.com/voice.amr"

    def test_no_voice_returns_none(self):
        from plugins.deepseek.stt import extract_voice_url
        event = MagicMock()
        seg = MagicMock()
        seg.type = "text"
        seg.data = {"text": "hello"}
        event.get_message.return_value = [seg]
        url = extract_voice_url(event)
        assert url is None

    def test_empty_url_returns_none(self):
        from plugins.deepseek.stt import extract_voice_url
        event = MagicMock()
        seg = MagicMock()
        seg.type = "record"
        seg.data = {"url": "", "file": ""}
        event.get_message.return_value = [seg]
        url = extract_voice_url(event)
        assert url is None


class TestConvertToPcm:
    """测试 PCM 转换逻辑。"""

    @pytest.mark.asyncio
    async def test_convert_calls_ffmpeg(self):
        """验证调用 ffmpeg 命令参数正确。"""
        from plugins.deepseek.stt import _convert_to_pcm
        with patch("asyncio.create_subprocess_exec") as mock_exec:
            proc = AsyncMock()
            proc.communicate.return_value = (b"", b"")
            proc.returncode = 0
            mock_exec.return_value = proc

            with patch("os.path.exists", return_value=True), \
                 patch("os.path.getsize", return_value=1000):
                result = await _convert_to_pcm("/tmp/test.amr")

            assert result == "/tmp/test.pcm"
            # 验证 ffmpeg 参数
            call_args = mock_exec.call_args[0]
            assert "ffmpeg" in call_args
            assert "-f" in call_args
            assert "s16le" in call_args
            assert "16000" in call_args


class TestCallBaiduStt:
    """测试百度 STT API 调用逻辑。"""

    @pytest.mark.asyncio
    async def test_success_response(self):
        from plugins.deepseek.stt import _call_baidu_stt

        # Mock aiofiles.open
        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"\x00" * 1000)
        mock_af = MagicMock()
        mock_af.__aenter__ = AsyncMock(return_value=mock_file)
        mock_af.__aexit__ = AsyncMock(return_value=False)

        # Mock HTTP session.post response
        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"err_no": 0, "result": ["你好世界"]})
        mock_resp_cm = MagicMock()
        mock_resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_sess = AsyncMock()
        mock_sess.post = MagicMock(return_value=mock_resp_cm)

        with patch("plugins.deepseek.stt._get_baidu_vop_token", return_value="test_token"), \
             patch("plugins.deepseek.stt.aiofiles") as mock_aiofiles_mod, \
             patch("plugins.deepseek.stt.get_http_session", return_value=mock_sess):
            mock_aiofiles_mod.open = MagicMock(return_value=mock_af)
            result = await _call_baidu_stt("/tmp/test.pcm")
        assert result == "你好世界"

    @pytest.mark.asyncio
    async def test_no_token_returns_none(self):
        from plugins.deepseek.stt import _call_baidu_stt
        with patch("plugins.deepseek.stt._get_baidu_vop_token", return_value=""):
            result = await _call_baidu_stt("/tmp/test.pcm")
            assert result is None

    @pytest.mark.asyncio
    async def test_error_response_returns_none(self):
        from plugins.deepseek.stt import _call_baidu_stt

        mock_file = AsyncMock()
        mock_file.read = AsyncMock(return_value=b"\x00" * 1000)
        mock_af = MagicMock()
        mock_af.__aenter__ = AsyncMock(return_value=mock_file)
        mock_af.__aexit__ = AsyncMock(return_value=False)

        mock_resp = AsyncMock()
        mock_resp.json = AsyncMock(return_value={"err_no": 3304, "err_msg": "speech quality error"})
        mock_resp_cm = MagicMock()
        mock_resp_cm.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp_cm.__aexit__ = AsyncMock(return_value=False)

        mock_sess = AsyncMock()
        mock_sess.post = MagicMock(return_value=mock_resp_cm)

        with patch("plugins.deepseek.stt._get_baidu_vop_token", return_value="test_token"), \
             patch("plugins.deepseek.stt.aiofiles") as mock_aiofiles_mod, \
             patch("plugins.deepseek.stt.get_http_session", return_value=mock_sess):
            mock_aiofiles_mod.open = MagicMock(return_value=mock_af)
            result = await _call_baidu_stt("/tmp/test.pcm")
        assert result is None


class TestRecognizeVoice:
    """测试主入口流程。"""

    @pytest.mark.asyncio
    async def test_no_voice_returns_none(self):
        from plugins.deepseek.stt import recognize_voice
        event = MagicMock()
        seg = MagicMock()
        seg.type = "text"
        seg.data = {"text": "hello"}
        event.get_message.return_value = [seg]
        result = await recognize_voice(event)
        assert result is None

    @pytest.mark.asyncio
    async def test_full_pipeline(self):
        """模拟完整 STT 流程。"""
        from plugins.deepseek.stt import recognize_voice
        event = MagicMock()
        seg = MagicMock()
        seg.type = "record"
        seg.data = {"url": "https://example.com/voice.amr"}
        event.get_message.return_value = [seg]

        with patch("plugins.deepseek.stt.download_voice", return_value="/tmp/voice.amr") as mock_dl, \
             patch("plugins.deepseek.stt._convert_to_pcm", return_value="/tmp/voice.pcm") as mock_pcm, \
             patch("plugins.deepseek.stt._call_baidu_stt", return_value="你好世界") as mock_stt, \
             patch("asyncio.create_task"):
            result = await recognize_voice(event)

        assert result == "你好世界"
        mock_dl.assert_called_once()
        mock_pcm.assert_called_once()
        mock_stt.assert_called_once_with("/tmp/voice.pcm")

    @pytest.mark.asyncio
    async def test_download_fail_returns_none(self):
        from plugins.deepseek.stt import recognize_voice
        event = MagicMock()
        seg = MagicMock()
        seg.type = "record"
        seg.data = {"url": "https://example.com/voice.amr"}
        event.get_message.return_value = [seg]

        with patch("plugins.deepseek.stt.download_voice", return_value=None):
            result = await recognize_voice(event)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
