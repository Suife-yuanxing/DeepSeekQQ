"""DeepSeek QQ Bot 插件入口——林念念。
注册消息处理器、启动/关闭钩子、语音文件服务。

惰性初始化：仅在 NoneBot 运行时（被 nonebot 加载）才注册消息处理器。
独立 FastAPI 进程（8766，api_platform.server）导入子模块时不再触发
NoneBot 初始化，避免 ValueError: NoneBot has not been initialized。
"""
try:
    from nonebot import on_message
    from nonebot.adapters.onebot.v11 import Bot
    from nonebot.adapters.onebot.v11 import MessageEvent
    from nonebot import get_driver
    _HAS_NONEBOT = True
    # 探测 NoneBot 是否已真正初始化（被 driver 加载时才初始化）。
    # conftest.py 会 mock nonebot 为空壳（get_driver 返回 MagicMock），
    # 此时 get_driver() 不抛异常但 server_app 是 None —— 视为无 nonebot。
    try:
        _drv = get_driver()
        _app = getattr(_drv, "server_app", None)
        if _app is None or not hasattr(_app, "add_middleware"):
            _HAS_NONEBOT = False
    except Exception:
        _HAS_NONEBOT = False
except Exception:
    _HAS_NONEBOT = False

if _HAS_NONEBOT:
    from . import startup
    from . import web_admin  # 管理后台 Web UI
    from . import handler     # 触发 stage 注册（填充 _PIPELINE）
    from .pipeline import handle_chat
    from .message_debounce import debouncer

    chat_handler = on_message(priority=5, block=False)

    @chat_handler.handle()
    async def _chat_handler(bot: Bot, event: MessageEvent):
        # 使用防抖：4秒窗口内合并消息，避免连续消息重复回复
        await debouncer.add_message(bot, event, handle_chat)
else:
    # 非 NoneBot 运行时（如独立 FastAPI 8766 进程）—— 不注册处理器，
    # 子模块（db_core / db_platform / api / api_platform）仍可被正常导入。
    pass
