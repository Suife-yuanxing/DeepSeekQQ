"""LLM 并发队列 — Phase 0.6。

平台 Key 有限流：FIFO 队列，≤5 并发，排队 >10 直接拒绝。
用户自带 Key 不限流。

用法：
    from .llm_queue import acquire_slot, release_slot

    slot = await acquire_slot(is_platform_key=True, timeout=30)
    if slot is None:
        return "排队太长了，请稍后再说喵~"
    try:
        result = await call_deepseek_api(...)
    finally:
        release_slot(slot)
"""
import asyncio
import time
import uuid
from typing import Optional

# 平台 Key 限制
_PLATFORM_MAX_CONCURRENT = 5
_PLATFORM_MAX_QUEUE = 10

# 运行时状态
_platform_active: int = 0
_platform_queue: dict[str, tuple[asyncio.Event, float]] = {}  # slot_id → (event, enqueue_time)
_lock = asyncio.Lock()

# 可观测性
_stats = {
    "total_acquired": 0,
    "total_rejected": 0,
    "total_timeouts": 0,
    "peak_active": 0,
    "total_wait_ms": 0.0,
}


async def acquire_slot(is_platform_key: bool = True, timeout: float = 30.0) -> Optional[str]:
    """获取 LLM 调用槽位。返回 slot_id 或 None（排队太长/超时）。"""
    global _platform_active

    if not is_platform_key:
        # 用户自带 Key：不限流
        _stats["total_acquired"] += 1
        return f"user_{uuid.uuid4().hex[:8]}"

    async with _lock:
        if _platform_active < _PLATFORM_MAX_CONCURRENT:
            _platform_active += 1
            _stats["total_acquired"] += 1
            if _platform_active > _stats["peak_active"]:
                _stats["peak_active"] = _platform_active
            return f"plat_{uuid.uuid4().hex[:8]}"
        if len(_platform_queue) >= _PLATFORM_MAX_QUEUE:
            _stats["total_rejected"] += 1
            return None
        slot_id = f"q_{uuid.uuid4().hex[:8]}"
        event = asyncio.Event()
        enqueued = time.time()
        _platform_queue[slot_id] = (event, enqueued)

    # 排队等待
    try:
        await asyncio.wait_for(event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        async with _lock:
            _platform_queue.pop(slot_id, None)
        _stats["total_timeouts"] += 1
        return None

    wait_ms = (time.time() - enqueued) * 1000
    _stats["total_wait_ms"] += wait_ms
    _stats["total_acquired"] += 1
    return slot_id


async def release_slot(slot_id: str) -> None:
    """释放槽位，如有排队者则唤醒第一个。"""
    global _platform_active

    if not slot_id.startswith("plat_"):
        return  # 自带 Key 不限流，无需释放

    async with _lock:
        _platform_active = max(0, _platform_active - 1)
        # 唤醒下一个等待者
        if _platform_queue:
            next_slot, (event, _) = next(iter(_platform_queue.items()))
            del _platform_queue[next_slot]
            _platform_active += 1
            if _platform_active > _stats["peak_active"]:
                _stats["peak_active"] = _platform_active
            event.set()


def get_queue_stats() -> dict:
    """获取队列统计（供 /admin/api/status 和仪表盘聚合）。"""
    return {
        "active": _platform_active,
        "queued": len(_platform_queue),
        "max_concurrent": _PLATFORM_MAX_CONCURRENT,
        "stats": dict(_stats),
    }


async def queue_context(is_platform_key: bool = True, timeout: float = 30.0):
    """异步上下文管理器：自动获取/释放槽位。

    async with queue_context() as acquired:
        if acquired:
            await call_deepseek_api(...)
    """
    class QueueGuard:
        def __init__(self, is_platform, t):
            self._is_platform = is_platform
            self._timeout = t
            self.slot_id = None

        async def __aenter__(self):
            self.slot_id = await acquire_slot(self._is_platform, self._timeout)
            return self.slot_id

        async def __aexit__(self, *a):
            if self.slot_id:
                await release_slot(self.slot_id)

    return QueueGuard(is_platform_key, timeout)
