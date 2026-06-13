"""Stage: 插件钩子 — 运行已注册的插件 on_message 回调。"""
from typing import Optional

from nonebot import logger

from ..pipeline import _SKIP
from ..pipeline import ChatContext
from ..pipeline import stage
from ..plugin_manager import get_enabled_plugins


@stage("plugins")
async def _stage_plugins(ctx: ChatContext) -> Optional[str]:
    for plugin in get_enabled_plugins():
        try:
            result = await plugin.on_message(ctx)
            if result is _SKIP:
                return _SKIP
        except Exception as e:
            logger.error(f"[插件] {plugin.meta.name} 执行失败: {e}")
    return None
