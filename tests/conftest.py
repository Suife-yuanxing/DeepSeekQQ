"""共享 Mock 配置 — 让测试可以在没有 nonebot 运行环境的情况下导入插件模块。"""
import sys
import types

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
