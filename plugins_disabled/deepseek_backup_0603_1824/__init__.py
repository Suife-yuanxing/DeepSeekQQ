"""DeepSeek 猫娘插件入口。
注册消息处理器、启动/关闭钩子、语音文件服务。
"""

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .handler import handle_chat
from . import startup

chat_handler = on_message(priority=5, block=False)

@chat_handler.handle()
async def _chat_handler(bot: Bot, event: MessageEvent):
    await handle_chat(bot, event)
