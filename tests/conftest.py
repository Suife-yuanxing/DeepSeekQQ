"""共享 Mock 配置 — 让测试可以在没有 nonebot 运行环境的情况下导入插件模块。

============================================================
Mock 数据管理方案
============================================================
本文件提供三层 mock 管理：

1. **全局永久 Mock**（模块顶层）
   - nonebot / fastapi / onebot 适配器
   - 所有测试都需要，无需清理

2. **Mock 注册机制**（register_project_mock / unregister_project_mocks）
   - 按需注册项目内部模块 mock
   - 用 _safe_module_mock() 创建带 __getattr__ 兜底的 mock
   - 注册表追踪所有 mock，支持一键清理

3. **标签分类体系**（pytest.ini 定义 markers）
   - unit:       纯逻辑测试，无 I/O
   - integration: 需要真实服务（如 aiosqlite :memory:）
   - slow:       含 asyncio.sleep / time.sleep
   - needs_db:   涉及数据库接口（即使已 mock）
   - needs_llm:  调用 LLM API
   - needs_network: 需要网络访问
============================================================
"""
import sys
import types

# ============================================================
# 第一层：全局永久 Mock（所有测试共享，无需清理）
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
# 第二层：Mock 注册机制（按需注册，支持分类管理和一键清理）
# ============================================================

# 注册表：{module_name: original_module_or_None}
_PROJECT_MOCK_REGISTRY: dict = {}


def _safe_module_mock(name: str, **attrs):
    """创建安全的模块 mock：任何未显式设置的属性自动返回 MagicMock。

    避免因 mock 属性不全导致其他模块导入失败。
    例如：mock config 缺少某个属性 → 返回 MagicMock 而不是抛出 AttributeError。

    用法：
        mock = _safe_module_mock("plugins.deepseek.config", SHARE_TTL=1800)
        sys.modules["plugins.deepseek.config"] = mock
    """
    from unittest.mock import MagicMock
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)

    def _fallback_getattr(attr_name):
        if attr_name.startswith("_"):
            raise AttributeError(attr_name)
        return MagicMock()

    mod.__getattr__ = _fallback_getattr
    return mod


def register_project_mock(name: str, mock=None, **attrs):
    """注册项目模块 mock，同时注入 sys.modules。

    传入 mock 对象直接使用；传入 **attrs 则用 _safe_module_mock 创建。

    返回注册的 mock 对象。

    用法：
        # 方式1：传属性自动创建
        mock = register_project_mock("plugins.deepseek.config", SHARE_TTL=1800)

        # 方式2：传自定义 mock
        mock = register_project_mock("plugins.deepseek.utils", LRUDict=MyLRUDict)
    """
    if mock is None:
        mock = _safe_module_mock(name, **attrs)

    # 保存原始模块（如果存在）
    if name in sys.modules and name not in _PROJECT_MOCK_REGISTRY:
        _PROJECT_MOCK_REGISTRY[name] = sys.modules[name]
    else:
        _PROJECT_MOCK_REGISTRY[name] = None  # 标记：原来不存在

    sys.modules[name] = mock
    return mock


def unregister_project_mocks():
    """清理所有通过 register_project_mock 注册的 mock，恢复 sys.modules 原状。"""
    for name, original in _PROJECT_MOCK_REGISTRY.items():
        if original is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = original
    _PROJECT_MOCK_REGISTRY.clear()


def list_registered_mocks():
    """列出当前注册表中所有 mock 模块名。"""
    return sorted(_PROJECT_MOCK_REGISTRY.keys())
