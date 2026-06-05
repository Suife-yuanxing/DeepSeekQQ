"""功能⑤：API max_tokens 覆盖测试。"""
import pytest
import inspect
from plugins.deepseek.api import call_deepseek_api


class TestApiMaxTokensOverride:
    """测试 call_deepseek_api 的 max_tokens 参数。"""

    def test_signature_has_max_tokens(self):
        """确认函数签名包含 max_tokens 参数。"""
        sig = inspect.signature(call_deepseek_api)
        assert "max_tokens" in sig.parameters

    def test_max_tokens_default_none(self):
        """默认值为 None（使用 task_type 决定）。"""
        sig = inspect.signature(call_deepseek_api)
        param = sig.parameters["max_tokens"]
        assert param.default is None

    def test_max_tokens_in_docstring(self):
        """确认文档提及 max_tokens 覆盖功能。"""
        doc = call_deepseek_api.__doc__
        assert "max_tokens" in doc
