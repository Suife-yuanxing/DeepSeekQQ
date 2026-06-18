"""server.py FastAPI 端点测试 — H2 补强。

测试健康检查、统计汇总、错误处理、超时中间件。
"""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# 在导入 server 之前 mock 掉重依赖模块
import sys
sys.modules["tools.tokenlens.advisor"] = MagicMock()
sys.modules["tools.tokenlens.summary"] = MagicMock()
sys.modules["tools.tokenlens.config"] = MagicMock()

import tools.tokenlens.server as server_mod
from tools.tokenlens.server import app
from tools.tokenlens.parser import Aggregator


@pytest.fixture(autouse=True)
def reset_aggregator():
    """每个测试前后重置 Aggregator 全局状态。"""
    server_mod._aggregator = None
    yield
    server_mod._aggregator = None


def make_mock_aggregator():
    """创建一个带有基本数据的 mock Aggregator。"""
    agg = MagicMock(spec=Aggregator)
    agg.last_scan_time = "2026-06-18T12:00:00"
    agg.get_projects.return_value = ["DeepSeekQQ", "TokenLens"]
    agg._records = [MagicMock() for _ in range(5)]
    agg.scan.return_value = {"new_records": 5, "total": 10}
    return agg


# ═══════════════════════════════════════════════════════════════
# 同步测试（TestClient）
# ═══════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    """GET /api/health"""

    def test_health_initializing(self):
        """Aggregator 未初始化时返回 initializing 状态"""
        server_mod._aggregator = None
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "initializing"
        assert data["projects"] == 0

    def test_health_ok(self):
        """Aggregator 已初始化时返回 ok 状态"""
        mock_agg = make_mock_aggregator()
        server_mod._aggregator = mock_agg
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["projects"] == 2
        assert "DeepSeekQQ" in data["projects_list"]


class TestRefreshEndpoint:
    """GET /api/refresh"""

    def test_refresh_requires_aggregator(self):
        """未初始化时返回 503"""
        server_mod._aggregator = None
        client = TestClient(app)
        resp = client.get("/api/refresh")
        assert resp.status_code == 503

    def test_refresh_triggers_scan(self):
        """已初始化时触发扫描并返回结果"""
        server_mod._aggregator = make_mock_aggregator()
        client = TestClient(app)
        resp = client.get("/api/refresh")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "summary" in data
        assert data["records"] == 5


class TestErrorHandling:
    """错误处理测试 — H2"""

    def test_invalid_endpoint_404(self):
        """不存在的端点返回 404"""
        client = TestClient(app)
        resp = client.get("/api/nonexistent-endpoint")
        assert resp.status_code == 404

    def test_missing_aggregator_503(self):
        """未初始化 Aggregator 时数据端点返回 503"""
        server_mod._aggregator = None
        client = TestClient(app)
        assert client.get("/api/refresh").status_code == 503
        assert client.get("/api/stats").status_code == 503
        # /api/summary 先查缓存再查 aggregator，
        # mock 的 summary 模块不受 aggregator 控制，故单独测试参数校验
        assert client.get("/api/summary").status_code == 422  # 缺少必填 session 参数


class TestModelEndpoint:
    """GET /api/models"""

    def test_models_requires_aggregator(self):
        """未初始化时返回 503"""
        server_mod._aggregator = None
        client = TestClient(app)
        resp = client.get("/api/models")
        assert resp.status_code == 503


# ═══════════════════════════════════════════════════════════════
# 异步测试（M5: 超时中间件）
# ═══════════════════════════════════════════════════════════════

class TestTimeoutMiddleware:
    """M5: 超时中间件测试"""

    @pytest.mark.asyncio
    async def test_timeout_returns_504(self):
        """验证超时中间件在请求超时时返回 504"""
        from tools.tokenlens.server import timeout_middleware

        async def slow_handler(_request):
            await asyncio.sleep(9999)
            return MagicMock()

        with patch("tools.tokenlens.server._REQUEST_TIMEOUT", 0.01):
            request = MagicMock()
            request.method = "GET"
            request.url.path = "/api/slow"

            resp = await timeout_middleware(request, slow_handler)

            assert resp.status_code == 504
            data = resp.body.decode()
            assert "超时" in data
