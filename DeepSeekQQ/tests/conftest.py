"""共享 Mock 配置 — 让测试可以在没有 nonebot 运行环境的情况下导入插件模块。

提供两层支持：
1. **全局永久 Mock**（模块顶层） — nonebot / fastapi / onebot 适配器，所有测试共享
2. **safe_module_mock() 工具函数** — 创建带 __getattr__ 兜底的模块 mock，各测试按需使用

用法（在测试文件中）：
    from tests.conftest import safe_module_mock

    mock = safe_module_mock("plugins.deepseek.config", SHARE_TTL=1800)
    sys.modules["plugins.deepseek.config"] = mock
"""

import sys
import types
from unittest.mock import MagicMock

# ============================================================
# 全局永久 Mock（所有测试共享，无需清理）
# ============================================================

# === Mock nonebot 核心 ===
mock_nonebot = types.ModuleType("nonebot")
mock_logger = types.ModuleType("nonebot.logger")
mock_logger.info = lambda *a, **k: None
mock_logger.warning = lambda *a, **k: None
mock_logger.error = lambda *a, **k: None
mock_logger.debug = lambda *a, **k: None
mock_nonebot.logger = mock_logger


class _MockMatcher:
    def handle(self):
        return lambda f: f
    def __call__(self, *a, **k):
        return self


mock_nonebot.on_message = lambda *a, **k: _MockMatcher()


def _noop_decorator(*a, **k):
    def wrapper(f):
        return f
    return wrapper


_mock_driver = types.SimpleNamespace(
    config=types.SimpleNamespace(
        deepseek_api_key="test", deepseek_model="test", deepseek_base_url="http://test",
        baidu_tts_ak="", baidu_tts_sk="", deepseek_db_path=":memory:",
        deepseek_voice_dir="./data/voice", host="0.0.0.0", port=8080,
        my_qq="12345", tavily_api_key="", weather_api_key="",
        qwen_vl_api_key="", qwen_vl_model="qwen-vl-plus",
    ),
    on_startup=_noop_decorator,
    on_shutdown=_noop_decorator,
    server_app=None,
)
mock_nonebot.get_driver = lambda: _mock_driver
mock_nonebot.get_bots = lambda: {}
sys.modules["nonebot"] = mock_nonebot
sys.modules["nonebot.logger"] = mock_logger

# === Mock nonebot.adapters ===
mock_adapters = types.ModuleType("nonebot.adapters")
mock_onebot = types.ModuleType("nonebot.adapters.onebot")
mock_v11 = types.ModuleType("nonebot.adapters.onebot.v11")
mock_v11.Bot = type("Bot", (), {})
mock_v11.MessageEvent = type("MessageEvent", (), {})
mock_v11.GroupMessageEvent = type("GroupMessageEvent", (), {})
mock_v11.PrivateMessageEvent = type("PrivateMessageEvent", (), {})
mock_v11.Message = type("Message", (list,), {})
mock_v11.MessageSegment = type("MessageSegment", (), {"text": staticmethod(lambda t: t)})
mock_adapters.onebot = mock_onebot
mock_onebot.v11 = mock_v11
sys.modules["nonebot.adapters"] = mock_adapters
sys.modules["nonebot.adapters.onebot"] = mock_onebot
sys.modules["nonebot.adapters.onebot.v11"] = mock_v11

# === Mock fastapi ===
mock_fastapi = types.ModuleType("fastapi")
mock_fastapi.FastAPI = type("FastAPI", (), {})
mock_fastapi.responses = types.ModuleType("fastapi.responses")
mock_fastapi.responses.FileResponse = type("FileResponse", (), {})
sys.modules["fastapi"] = mock_fastapi
sys.modules["fastapi.responses"] = mock_fastapi.responses


# ============================================================
# 工具函数：创建安全的模块 mock
# ============================================================

def safe_module_mock(name: str, **attrs):
    """创建安全的模块 mock：未显式设置的属性自动返回 MagicMock。

    避免因 mock 属性不全导致其他模块导入时抛出 AttributeError。

    用法：
        mock = safe_module_mock("plugins.deepseek.config", SHARE_TTL=1800)
        sys.modules["plugins.deepseek.config"] = mock
    """
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)

    def _fallback_getattr(attr_name):
        if attr_name.startswith("_"):
            raise AttributeError(attr_name)
        return MagicMock()

    mod.__getattr__ = _fallback_getattr
    return mod


# ============================================================
# Pytest 配置
# ============================================================

def pytest_configure(config):
    config.addinivalue_line(
        "markers", "unit: 纯逻辑测试，无 I/O"
    )
    config.addinivalue_line(
        "markers", "integration: 需要真实服务（如 aiosqlite :memory:）"
    )
    config.addinivalue_line(
        "markers", "slow: 含 asyncio.sleep / time.sleep"
    )
    config.addinivalue_line(
        "markers", "needs_db: 涉及数据库接口（即使已 mock）"
    )
    config.addinivalue_line(
        "markers", "needs_llm: 调用 LLM API"
    )
    config.addinivalue_line(
        "markers", "needs_network: 需要网络访问"
    )
