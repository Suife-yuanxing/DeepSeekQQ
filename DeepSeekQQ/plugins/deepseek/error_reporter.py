"""集中错误收集器 — 为 fire-and-forget 任务提供可插拔的错误处理。

所有后台任务应通过 safe_task() 创建，任何未捕获异常都会路由到此处注册的处理器。
模块可在启动时注册自定义处理器（如发送通知、写入监控指标）。
"""

import asyncio
import traceback
from typing import Callable, Awaitable, Any

from nonebot import logger

# ═══════════════════════════════════════════════════════════════
# 错误处理器注册表
# ═══════════════════════════════════════════════════════════════

ErrorHandler = Callable[[str, BaseException, str | None], Awaitable[None]]
"""错误处理器签名: async (task_name, exception, traceback_str) -> None"""

_handlers: list[ErrorHandler] = []


def register_handler(handler: ErrorHandler) -> None:
    """注册后台错误处理器。处理器按注册顺序调用，单个处理器异常不影响其他。"""
    _handlers.append(handler)


def unregister_handler(handler: ErrorHandler) -> None:
    """移除已注册的错误处理器。"""
    try:
        _handlers.remove(handler)
    except ValueError:
        pass


async def _notify_handlers(task_name: str, exc: BaseException, tb_str: str | None) -> None:
    """通知所有注册的处理器（fire-and-forget，自身异常被静默捕获）。"""
    for handler in _handlers:
        try:
            await handler(task_name, exc, tb_str)
        except Exception:
            # 处理器异常不应级联
            pass


# ═══════════════════════════════════════════════════════════════
# 安全后台任务
# ═══════════════════════════════════════════════════════════════

_background_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    """task 完成回调：从引用集合移除 + 记录异常 + 通知处理器。"""
    _background_tasks.discard(task)
    exc = task.exception()
    if exc:
        task_name = task.get_name() or "unnamed"
        tb_str = "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)
        )
        logger.error(
            f"[后台任务] {task_name} 异常:\n{tb_str}"
        )
        # 调度异步通知（不阻塞回调）
        asyncio.create_task(_notify_handlers(task_name, exc, tb_str))


def safe_task(coro, *, name: str | None = None) -> asyncio.Task:
    """安全创建后台任务：保存引用 + 捕获异常 + 通知处理器。

    替代裸 asyncio.create_task()，避免：
    1. task 被 GC 回收导致 "Task was destroyed but it is pending" 警告
    2. task 内部异常被静默丢弃
    3. 无统一错误通知渠道

    Args:
        coro: 协程对象
        name: 任务名称（可选），用于错误日志标识
    """
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


# ═══════════════════════════════════════════════════════════════
# 方便函数
# ═══════════════════════════════════════════════════════════════

def pending_count() -> int:
    """返回当前待处理的后台任务数（用于监控/健康检查）。"""
    return len(_background_tasks)
