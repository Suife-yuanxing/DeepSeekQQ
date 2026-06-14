"""DeepSeek QQ Bot 插件入口——林念念。
注册消息处理器、启动/关闭钩子、语音文件服务。
"""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11 import MessageEvent

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
