"""circuit_breaker 测试 — 熔断器状态机。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
import pytest_asyncio
from plugins.deepseek.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_initial_state_closed(self):
        cb = CircuitBreaker("test", fail_threshold=3, recovery_seconds=10)
        assert cb.state == "closed"
        assert cb.fail_count == 0

    @pytest.mark.asyncio
    async def test_success_resets_fail_count(self):
        cb = CircuitBreaker("test", fail_threshold=3, recovery_seconds=10)
        cb.fail_count = 2

        async def success_func():
            return "ok"

        result = await cb.call(success_func)
        assert result == "ok"
        assert cb.fail_count == 0
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_failure_increments_count(self):
        cb = CircuitBreaker("test", fail_threshold=3, recovery_seconds=10)

        async def fail_func():
            raise ValueError("fail")

        for _ in range(2):
            result = await cb.call(fail_func)
            assert result is None
        assert cb.fail_count == 2
        assert cb.state == "closed"

    @pytest.mark.asyncio
    async def test_opens_after_threshold(self):
        cb = CircuitBreaker("test", fail_threshold=3, recovery_seconds=10)

        async def fail_func():
            raise ValueError("fail")

        for _ in range(3):
            await cb.call(fail_func)
        assert cb.state == "open"

    @pytest.mark.asyncio
    async def test_open_circuit_skips_call(self):
        cb = CircuitBreaker("test", fail_threshold=1, recovery_seconds=10)

        async def fail_func():
            raise ValueError("fail")

        async def success_func():
            return "ok"

        await cb.call(fail_func)
        assert cb.state == "open"

        result = await cb.call(success_func)
        assert result is None

    @pytest.mark.asyncio
    async def test_fallback_called_when_open(self):
        cb = CircuitBreaker("test", fail_threshold=1, recovery_seconds=10)

        async def fail_func():
            raise ValueError("fail")

        await cb.call(fail_func)
        assert cb.state == "open"

        result = await cb.call(lambda: None, fallback=lambda: "fallback_value")
        assert result == "fallback_value"

    def test_reset(self):
        cb = CircuitBreaker("test", fail_threshold=1, recovery_seconds=10)
        cb.fail_count = 5
        cb.state = "open"
        cb.reset()
        assert cb.state == "closed"
        assert cb.fail_count == 0

    def test_status(self):
        cb = CircuitBreaker("my_api", fail_threshold=5, recovery_seconds=120)
        status = cb.status()
        assert status["name"] == "my_api"
        assert status["fail_threshold"] == 5
        assert status["recovery_seconds"] == 120


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
