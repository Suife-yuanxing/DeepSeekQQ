"""统一熔断器 — 防止外部 API 连续失败拖慢整个系统。

用法:
    tavily_breaker = CircuitBreaker("tavily", fail_threshold=3, recovery_seconds=120)
    result = await tavily_breaker.call(api_func, arg1, arg2, fallback=lambda: None)

状态机:
    closed → (连续失败 >= threshold) → open → (等待 recovery_seconds) → half_open
    half_open → 成功 → closed
    half_open → 失败 → open (重置计时)
"""
import asyncio
import inspect
import time
from typing import Any
from typing import Callable
from typing import Optional

from nonebot import logger


async def _call_fallback(fallback: Callable) -> Any:
    """统一处理 fallback 调用，支持同步和异步函数。"""
    if inspect.iscoroutinefunction(fallback):
        return await fallback()
    return fallback()


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
                self.fail_count = 0  # 半开状态重置失败计数
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
                return await _call_fallback(fallback)
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
                    return await _call_fallback(fallback)
                except Exception as fallback_err:
                    logger.warning(f"[熔断] {self.name} fallback 也失败了: {fallback_err}")
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


# ============================================================
# 全局熔断器实例（按外部服务隔离）
# ============================================================

# URL 抓取服务 — 开放 Web，单点故障不影响其他
_share_breaker = CircuitBreaker("share_fetch", fail_threshold=5, recovery_seconds=120)

# 和风天气 API — 免费版，可能有配额或 IP 限制
_weather_breaker = CircuitBreaker("qweather", fail_threshold=3, recovery_seconds=180)

# 抖音热搜 — 可能反爬或限流
_douyin_breaker = CircuitBreaker("douyin", fail_threshold=4, recovery_seconds=300)

# B站热搜 — 较稳定，阈值宽松
_bilibili_breaker = CircuitBreaker("bilibili", fail_threshold=3, recovery_seconds=180)

# Tavily 搜索 API — 付费 API，关键服务
_tavily_breaker = CircuitBreaker("tavily_search", fail_threshold=3, recovery_seconds=90)

# P0-9: DeepSeek API 熔断器 — 核心对话 API，快速熔断快速恢复
_deepseek_breaker = CircuitBreaker("deepseek_api", fail_threshold=3, recovery_seconds=30)

# P0-9: Ollama 本地 API 熔断器 — 本地服务，快速熔断
_ollama_breaker = CircuitBreaker("ollama_api", fail_threshold=2, recovery_seconds=60)


def get_breaker(service: str):
    """获取指定服务的熔断器实例（供外部模块使用）。

    Args:
        service: "share_fetch" | "qweather" | "douyin" | "bilibili" | "tavily_search"
                 | "deepseek_api" | "ollama_api"

    Returns:
        CircuitBreaker 实例，或 None（未知服务名）
    """
    return {
        "share_fetch": _share_breaker,
        "qweather": _weather_breaker,
        "douyin": _douyin_breaker,
        "bilibili": _bilibili_breaker,
        "tavily_search": _tavily_breaker,
        "deepseek_api": _deepseek_breaker,
        "ollama_api": _ollama_breaker,
    }.get(service)


# ============================================================
# P0-9: Ollama 可用性缓存（60s TTL，避免频繁 ping /api/tags）
# ============================================================

_ollama_available_cache = {
    "available": None,  # None=未检查, True/False
    "checked_at": 0.0,
}
_ollama_cache_lock = asyncio.Lock()
OLLAMA_CACHE_TTL = 60.0


async def is_ollama_available_cached() -> bool:
    """检查 Ollama 可用性（60s TTL 缓存）。

    避免每次 DeepSeek API 失败都去 ping Ollama /api/tags 端点。
    """
    now = time.time()
    cache = _ollama_available_cache

    if cache["available"] is not None and (now - cache["checked_at"]) < OLLAMA_CACHE_TTL:
        return cache["available"]

    async with _ollama_cache_lock:
        # 双重检查
        if cache["available"] is not None and (now - cache["checked_at"]) < OLLAMA_CACHE_TTL:
            return cache["available"]

        try:
            from .local_llm import check_ollama_available
            available = await check_ollama_available()
        except Exception:
            available = False

        cache["available"] = available
        cache["checked_at"] = time.time()
        logger.debug(f"[Ollama缓存] 可用性刷新: {available}")
        return available


def invalidate_ollama_cache():
    """手动失效 Ollama 可用性缓存。"""
    _ollama_available_cache["available"] = None
    _ollama_available_cache["checked_at"] = 0.0
