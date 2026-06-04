"""循环任务管理器测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from plugins.deepseek.loop_manager import LoopManager, LoopTask


class TestLoopManager:
    def test_register_task(self):
        mgr = LoopManager()
        async def dummy(): pass
        mgr.register("test", dummy, 60)
        assert "test" in mgr._tasks
        assert mgr._tasks["test"].interval == 60

    def test_get_status_empty(self):
        mgr = LoopManager()
        assert len(mgr.get_status()) == 0

    def test_get_status_with_tasks(self):
        mgr = LoopManager()
        async def dummy(): pass
        mgr.register("task1", dummy, 30)
        mgr.register("task2", dummy, 60)
        status = mgr.get_status()
        assert len(status) == 2

    def test_task_initial_state(self):
        task = LoopTask(name="test", coro_func=lambda: None, interval=60)
        assert task.status == "pending"
        assert task.error_count == 0


class TestLoopTaskExecution:
    @pytest.mark.asyncio
    async def test_single_run(self):
        mgr = LoopManager()
        counter = {"count": 0}
        async def counting_task():
            counter["count"] += 1
            if counter["count"] >= 2:
                mgr.stop("counter")
        mgr.register("counter", counting_task, 0.1)
        await mgr.start("counter")
        await asyncio.sleep(0.5)
        assert counter["count"] >= 2

    @pytest.mark.asyncio
    async def test_error_backoff(self):
        mgr = LoopManager()
        async def failing_task():
            raise ValueError("test error")
        mgr.register("fail", failing_task, 1.0)
        await mgr.start("fail")
        await asyncio.sleep(0.3)
        task = mgr._tasks["fail"]
        assert task.error_count >= 1
        mgr.stop("fail")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
