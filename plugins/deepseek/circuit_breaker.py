"""统一熔断器 — 防止外部 API 连续失败拖慢整个系统。

用法:
    tavily_breaker = CircuitBreaker("tavily", fail_threshold=3, recovery_seconds=120)
    result = await tavily_breaker.call(api_func, arg1, arg2, fallback=lambda: None)

状态机:
    closed → (连续失败 >= threshold) → open → (等待 recovery_seconds) → half_open
    half_open → 成功 → closed
    half_open → 失败 → open (重置计时)
"""
import time
from typing import Callable, Any, Optional
from nonebot import logger


class CircuitBreaker:
    def __init__(self, name: str, fail_threshold: int = 3, recovery_seconds: int = 60):
        self.name = name
        self.fail_threshold = fail_threshold
        self.recovery_seconds = recovery_seconds
        self.fail_count = 0
        self.last_fail_time = 0.0
        self.state = "closed"  # closed / open / half_open

    def _is_open(self) -> bool:
        if self.state == "open":
            if time.time() - self.last_fail_time > self.recovery_seconds:
                self.state = "half_open"
                return False
            return True
        return False

    async def call(self, func: Callable, *args, fallback: Optional[Callable] = None, **kwargs) -> Any:
        """调用目标函数，自动熔断和降级。

        Args:
            func: 异步函数
            fallback: 熔断时的降级函数（同步或异步）

        Returns:
            func 的返回值，或 fallback 的返回值，或 None
        """
        if self._is_open():
            logger.debug(f"[熔断] {self.name} 熔断中，跳过调用")
            if fallback:
                return fallback() if callable(fallback) else fallback
            return None

        try:
            result = await func(*args, **kwargs)
            # 成功：重置计数
            if self.fail_count > 0:
                logger.info(f"[熔断] {self.name} 恢复正常")
            self.fail_count = 0
            self.state = "closed"
            return result
        except Exception as e:
            self.fail_count += 1
            self.last_fail_time = time.time()
            if self.fail_count >= self.fail_threshold:
                self.state = "open"
                logger.warning(
                    f"[熔断] {self.name} 连续失败 {self.fail_count} 次，"
                    f"熔断 {self.recovery_seconds}s | 最后错误: {e}"
                )
            else:
                logger.debug(f"[熔断] {self.name} 失败 {self.fail_count}/{self.fail_threshold}: {e}")
            if fallback:
                try:
                    return fallback() if callable(fallback) else fallback
                except Exception:
                    pass
            return None

    def reset(self):
        """手动重置熔断器。"""
        self.fail_count = 0
        self.state = "closed"
        self.last_fail_time = 0.0

    def status(self) -> dict:
        return {
            "name": self.name,
            "state": self.state,
            "fail_count": self.fail_count,
            "fail_threshold": self.fail_threshold,
            "recovery_seconds": self.recovery_seconds,
        }
