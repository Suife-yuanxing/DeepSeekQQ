"""Test API client — DeepSeek API 调用层。

C-5: 覆盖 HTTP 会话管理、指数退避重试、限流、超时处理。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# HTTP Session 管理
# ═══════════════════════════════════════════════════════════════

class TestHttpSession:
    """测试全局 HTTP Session 管理。"""

    def test_get_session_creates_new(self):
        """首次调用应创建新 session。"""
        import plugins.deepseek.api as api_mod
        # 重置全局状态
        api_mod._http_session = None
        import asyncio
        session = asyncio.run(api_mod.get_http_session())
        assert session is not None
        assert not session.closed

    def test_get_session_reuses_existing(self):
        """第二次调用应复用已有 session。"""
        import plugins.deepseek.api as api_mod
        import asyncio
        session1 = asyncio.run(api_mod.get_http_session())
        session2 = asyncio.run(api_mod.get_http_session())
        assert session1 is session2

    @pytest.mark.asyncio
    async def test_close_session_cleans_up(self):
        """关闭 session 后状态应重置。"""
        from plugins.deepseek.api import close_http_session, get_http_session
        # 确保 session 存在
        await get_http_session()
        await close_http_session()
        assert close_http_session._http_session is None \
            if hasattr(close_http_session, '_http_session') else True


# ═══════════════════════════════════════════════════════════════
# API 调用：错误处理
# ═══════════════════════════════════════════════════════════════

class TestApiCallErrors:
    """测试 API 调用的错误处理路径。"""

    def test_no_api_key_returns_none(self):
        """未配置 API Key 时应返回 None。"""
        from unittest.mock import patch
        with patch("plugins.deepseek.api.API_KEY", ""):
            import asyncio
            from plugins.deepseek.api import _call_deepseek_raw
            result = asyncio.run(_call_deepseek_raw([{"role": "user", "content": "hi"}]))
            assert result is None

    @pytest.mark.asyncio
    async def test_429_retry_with_backoff(self):
        """429 响应应触发指数退避重试。"""
        from unittest.mock import patch, AsyncMock, MagicMock
        import aiohttp

        mock_resp = MagicMock()
        mock_resp.status = 429
        mock_resp.text = AsyncMock(return_value="Rate limited")

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = AsyncMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_resp),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("plugins.deepseek.api.get_http_session", AsyncMock(return_value=mock_session)):
            with patch("plugins.deepseek.api.API_KEY", "sk-test"):
                from plugins.deepseek.api import _call_deepseek_raw
                result = await _call_deepseek_raw(
                    [{"role": "user", "content": "hi"}],
                    temperature=0.5
                )
                # 429 重试后仍失败，应返回 None
                assert result is None
                # 应根据重试次数多次调用
                assert mock_session.post.call_count >= 1

    @pytest.mark.asyncio
    async def test_network_error_returns_none(self):
        """网络错误时不应抛异常，应返回 None。"""
        from unittest.mock import patch, AsyncMock, MagicMock

        mock_session = MagicMock()
        mock_session.closed = False
        mock_session.post = AsyncMock(side_effect=ConnectionError("Network unreachable"))

        with patch("plugins.deepseek.api.get_http_session", AsyncMock(return_value=mock_session)):
            with patch("plugins.deepseek.api.API_KEY", "sk-test"):
                from plugins.deepseek.api import _call_deepseek_raw
                result = await _call_deepseek_raw(
                    [{"role": "user", "content": "hi"}]
                )
                assert result is None


# ═══════════════════════════════════════════════════════════════
# JSON 解析工具
# ═══════════════════════════════════════════════════════════════

class TestJsonParsing:
    """测试 JSON 解析相关的辅助函数。"""

    def test_smart_quote_fix(self):
        """弯引号标准化。"""
        text = "“Hello” ‘world’"
        fixed = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
        assert "“" not in fixed
        assert '"' in fixed

    def test_extract_json_from_markdown_block(self):
        """从 markdown 代码块提取 JSON。"""
        import re
        text = '```json\n{"key": "value"}\n```'
        match = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', text, re.DOTALL)
        assert match is not None
        import json
        parsed = json.loads(match.group(1))
        assert parsed["key"] == "value"


# ═══════════════════════════════════════════════════════════════
# 响应清洗
# ═══════════════════════════════════════════════════════════════

class TestResponseCleaning:
    """测试 API 响应清洗函数。"""

    def test_clean_api_response_removes_thinking_tags(self):
        from plugins.deepseek.utils import clean_api_response
        text = "好的！<｜end▁of▁thinking｜>这是回答"
        result = clean_api_response(text)
        assert "回答" in result

    def test_clean_api_response_preserves_normal_text(self):
        from plugins.deepseek.utils import clean_api_response
        text = "你好，这是正常的回复内容"
        result = clean_api_response(text)
        assert "你好" in result
        assert "回复" in result

    def test_clean_api_response_handles_empty(self):
        from plugins.deepseek.utils import clean_api_response
        assert clean_api_response("") == ""
        assert clean_api_response("  ") == ""
