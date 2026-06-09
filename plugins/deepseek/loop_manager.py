"""自主循环任务管理器 — ECC autonomous-loops 风格。

统一管理所有后台循环任务：启动/暂停/恢复/状态查询/异常退避。
"""
import asyncio
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Coroutine
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger


@dataclass
class LoopTask:
    """循环任务状态。"""
    name: str
    coro_func: Callable[[], Coroutine[Any, Any, None]]
    interval: float                    # 基础间隔（秒）
    status: str = "pending"            # pending/running/paused/error
    last_run: float = 0.0
    last_error: str = ""
    error_count: int = 0
    total_runs: int = 0
    _task: Optional[asyncio.Task] = field(default=None, repr=False)
    _current_interval: float = 0.0     # 当前实际间隔（含退避）

    def __post_init__(self):
        self._current_interval = self.interval


class LoopManager:
    """统一的循环任务管理器。

    特性：
    - 异常自动退避：连续失败时指数增加间隔（最大 10x）
    - 状态查询：随时查看所有任务状态
    - 暂停/恢复：运行时控制
    - 异常通知：连续失败 3 次时记录警告
    """

    def __init__(self):
        self._tasks: Dict[str, LoopTask] = {}

    def register(self, name: str, coro_func: Callable, interval: float):
        """注册一个循环任务。"""
        self._tasks[name] = LoopTask(name=name, coro_func=coro_func, interval=interval)

    async def start(self, name: str, delay: float = 0):
        """启动一个已注册的任务。"""
        task = self._tasks.get(name)
        if not task:
            logger.error(f"[循环管理] 任务未注册: {name}")
            return
        if task._task and not task._task.done():
            return  # 已在运行
        if delay > 0:
            await asyncio.sleep(delay)
        task._task = asyncio.create_task(self._run_loop(task))
        task.status = "running"
        logger.info(f"[循环管理] 启动任务: {name} (间隔 {task.interval}s)")

    async def start_all(self):
        """启动所有已注册的任务。"""
        for name in self._tasks:
            await self.start(name)

    def stop(self, name: str):
        """停止一个任务。"""
        task = self._tasks.get(name)
        if task and task._task and not task._task.done():
            task._task.cancel()
            task.status = "paused"

    def get_status(self) -> List[Dict[str, Any]]:
        """获取所有任务状态。"""
        result = []
        for name, task in self._tasks.items():
            result.append({
                "name": name,
                "status": task.status,
                "interval": task.interval,
                "current_interval": round(task._current_interval, 1),
                "last_run": task.last_run,
                "error_count": task.error_count,
                "total_runs": task.total_runs,
                "last_error": task.last_error[:100] if task.last_error else "",
            })
        return result

    async def _run_loop(self, task: LoopTask):
        """单个任务的循环执行逻辑。"""
        while True:
            try:
                await task.coro_func()
                task.total_runs += 1
                task.last_run = time.time()
                task.error_count = 0
                task._current_interval = task.interval  # 成功后重置间隔
                task.status = "running"
            except asyncio.CancelledError:
                task.status = "paused"
                break
            except Exception as e:
                task.error_count += 1
                task.last_error = str(e)
                task.status = "error"
                # 指数退避：连续失败时增加间隔，最大 10 倍
                if task.error_count <= 3:
                    logger.error(f"[循环管理] {task.name} 异常(#{task.error_count}): {e}")
                else:
                    logger.warning(f"[循环管理] {task.name} 持续异常(#{task.error_count})，已静默")
                backoff = min(task.interval * (2 ** min(task.error_count, 4)), task.interval * 10)
                task._current_interval = backoff

            await asyncio.sleep(task._current_interval)


# 全局单例
loop_manager = LoopManager()
