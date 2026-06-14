"""插件化热加载系统（功能⑥）。

新功能写成独立模块，通过配置启停，不用改核心代码。

用法：
1. 在 plugins/deepseek/plugins/ 下创建 xxx.py
2. 定义 plugin = MyPlugin() 实例
3. Bot 启动时自动扫描加载

示例插件：
```python
from ..plugin_manager import BasePlugin, PluginMeta

class MyPlugin(BasePlugin):
    meta = PluginMeta(
        name="my_plugin",
        description="我的插件",
        stage_name="llm_call",  # 在哪个 stage 后插入
        priority=50,
    )

    async def on_message(self, ctx) -> Optional[str]:
        # 处理消息，返回 None 继续后续阶段
        return None

plugin = MyPlugin()
```
"""
import importlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


@dataclass
class PluginMeta:
    """插件元信息。"""
    name: str                           # 插件唯一名称
    description: str = ""               # 插件描述
    enabled: bool = True                # 是否启用
    priority: int = 50                  # 执行优先级（越小越先执行）
    stage_name: str = ""                # 插入到哪个 stage 之后（空字符串=末尾）
    # 注：stage_name 当前未在 pipeline 中实际使用，插件统一在固定位置执行
    # 后续 P2-2 将实现按 stage_name 动态插入


class BasePlugin:
    """插件基类。所有插件继承此类。"""
    meta: PluginMeta

    async def on_startup(self):
        """Bot 启动时调用。"""
        pass

    async def on_shutdown(self):
        """Bot 关闭时调用。"""
        pass

    async def on_message(self, ctx) -> Optional[str]:
        """消息处理入口。

        Args:
            ctx: ChatContext 实例

        Returns:
            None: 继续后续阶段
            _SKIP sentinel: 跳过后续阶段
        """
        return None


# ============================================================
# 插件注册表
# ============================================================

_plugins: List[BasePlugin] = []
_loaded: bool = False


def register_plugin(plugin: BasePlugin):
    """注册一个插件实例。"""
    if not isinstance(plugin, BasePlugin):
        logger.warning(f"[插件] 注册失败：{plugin} 不是 BasePlugin 实例")
        return
    # 检查重名
    for p in _plugins:
        if p.meta.name == plugin.meta.name:
            logger.warning(f"[插件] 跳过重复注册：{plugin.meta.name}")
            return
    _plugins.append(plugin)
    logger.info(f"[插件] 注册: {plugin.meta.name} ({plugin.meta.description})")


def get_enabled_plugins() -> List[BasePlugin]:
    """获取所有已启用的插件，按优先级排序。"""
    return sorted(
        [p for p in _plugins if p.meta.enabled],
        key=lambda p: p.meta.priority
    )


def get_plugin_by_name(name: str) -> Optional[BasePlugin]:
    """按名称获取插件。"""
    for p in _plugins:
        if p.meta.name == name:
            return p
    return None


def list_plugins() -> List[Dict[str, Any]]:
    """列出所有已注册插件的状态。"""
    return [
        {
            "name": p.meta.name,
            "description": p.meta.description,
            "enabled": p.meta.enabled,
            "priority": p.meta.priority,
            "stage_name": p.meta.stage_name,
        }
        for p in _plugins
    ]


def disable_plugin(name: str) -> bool:
    """禁用插件。"""
    p = get_plugin_by_name(name)
    if p:
        p.meta.enabled = False
        logger.info(f"[插件] 已禁用: {name}")
        return True
    return False


def enable_plugin(name: str) -> bool:
    """启用插件。"""
    p = get_plugin_by_name(name)
    if p:
        p.meta.enabled = True
        logger.info(f"[插件] 已启用: {name}")
        return True
    return False


# ============================================================
# 动态加载
# ============================================================

def load_plugins_from_dir(plugin_dir: str = None):
    """扫描目录，动态导入并注册插件。

    每个 .py 文件需要导出一个 `plugin` 变量（BasePlugin 实例）。
    以 _ 开头的文件会被跳过。
    """
    global _loaded
    if _loaded:
        return
    _loaded = True

    if plugin_dir is None:
        plugin_dir = os.path.join(os.path.dirname(__file__), "plugins")

    if not os.path.isdir(plugin_dir):
        logger.info(f"[插件] 目录不存在，跳过加载: {plugin_dir}")
        return

    # 确保 plugins 目录有 __init__.py
    init_file = os.path.join(plugin_dir, "__init__.py")
    if not os.path.exists(init_file):
        with open(init_file, "w", encoding="utf-8") as f:
            f.write("")

    loaded_count = 0
    for path in sorted(Path(plugin_dir).glob("*.py")):
        if path.name.startswith("_"):
            continue
        module_name = path.stem
        try:
            # 动态导入
            spec = importlib.util.spec_from_file_location(
                f"plugins.deepseek.plugins.{module_name}",
                str(path),
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                # 查找 plugin 实例
                if hasattr(mod, "plugin") and isinstance(mod.plugin, BasePlugin):
                    register_plugin(mod.plugin)
                    loaded_count += 1
                else:
                    logger.warning(f"[插件] {module_name} 没有导出 BasePlugin 实例 'plugin'")
        except Exception as e:
            logger.error(f"[插件] 加载 {module_name} 失败: {e}")

    logger.info(f"[插件] 扫描完成，成功加载 {loaded_count} 个插件")


# ============================================================
# 生命周期管理
# ============================================================

async def startup_all_plugins():
    """调用所有启用插件的 on_startup。"""
    for p in get_enabled_plugins():
        try:
            await p.on_startup()
            logger.info(f"[插件] 启动: {p.meta.name}")
        except Exception as e:
            logger.error(f"[插件] {p.meta.name} 启动失败: {e}")


async def shutdown_all_plugins():
    """调用所有插件的 on_shutdown。"""
    for p in _plugins:
        try:
            await p.on_shutdown()
        except Exception as e:
            logger.error(f"[插件] {p.meta.name} 关闭失败: {e}")
