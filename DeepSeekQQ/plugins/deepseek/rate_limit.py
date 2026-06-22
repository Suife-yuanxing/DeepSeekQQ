"""全局限流中间件 — Phase 0.12。

三层限流：
  1. Token 级: 30次/分（App 通道，按 JWT access_token）
  2. 用户级: 10次/分（App 通道，按 user_id）
  3. IP 级: 60次/分（全局兜底，所有通道）

QQ/微信通道不丢消息：限流触发时排队而非拒绝。
可观测：GET /api/v1/metrics 暴露限流统计。

用法（FastAPI 中间件）：
    from .rate_limit import RateLimitMiddleware
    app.add_middleware(RateLimitMiddleware)

用法（NoneBot2 Pipeline 内联，stage_rate_limit.py 已有，本模块补充 IP 级兜底）：
    from .rate_limit import check_ip_rate_limit
    if not await check_ip_rate_limit(client_ip):
        return _SKIP
"""
import asyncio
import time
from collections import defaultdict
from typing import Optional

# 限流配置
TOKEN_RATE = 30        # Token 级: 30次/分
TOKEN_WINDOW = 60      # 秒
USER_RATE = 10         # 用户级: 10次/分
USER_WINDOW = 60
IP_RATE = 60           # IP 级: 60次/分
IP_WINDOW = 60

# 滑动窗口计数器（生产换 Redis）
_token_windows: dict[str, list[float]] = defaultdict(list)
_user_windows: dict[str, list[float]] = defaultdict(list)
_ip_windows: dict[str, list[float]] = defaultdict(list)
_lock = asyncio.Lock()

# 可观测性
_rate_stats = {
    "token_rejected": 0,
    "user_rejected": 0,
    "ip_rejected": 0,
    "total_checked": 0,
}


def _prune(window: list[float], cutoff: float) -> list[float]:
    """删除窗口外的旧记录，返回剪枝后列表。"""
    return [t for t in window if t > cutoff]


async def check_token_rate(token: str) -> bool:
    """检查 Token 级限流，返回 True=通过。"""
    now = time.time()
    cutoff = now - TOKEN_WINDOW
    async with _lock:
        window = _prune(_token_windows[token], cutoff)
        _rate_stats["total_checked"] += 1
        if len(window) >= TOKEN_RATE:
            _rate_stats["token_rejected"] += 1
            return False
        window.append(now)
        _token_windows[token] = window
    return True


async def check_user_rate(user_id: int) -> bool:
    """检查用户级限流，返回 True=通过。"""
    now = time.time()
    cutoff = now - USER_WINDOW
    key = str(user_id)
    async with _lock:
        window = _prune(_user_windows[key], cutoff)
        if len(window) >= USER_RATE:
            _rate_stats["user_rejected"] += 1
            return False
        window.append(now)
        _user_windows[key] = window
    return True


async def check_ip_rate(client_ip: str) -> bool:
    """检查 IP 级限流（全局兜底），返回 True=通过。"""
    now = time.time()
    cutoff = now - IP_WINDOW
    async with _lock:
        window = _prune(_ip_windows[client_ip], cutoff)
        if len(window) >= IP_RATE:
            _rate_stats["ip_rejected"] += 1
            return False
        window.append(now)
        _ip_windows[client_ip] = window
    return True


def get_rate_stats() -> dict:
    """获取限流统计。"""
    return {
        "token_windows": len(_token_windows),
        "user_windows": len(_user_windows),
        "ip_windows": len(_ip_windows),
        "stats": dict(_rate_stats),
    }


class RateLimitMiddleware:
    """Starlette/ASGI 中间件，集成到 FastAPI（仅覆盖 /api/v1/* 路由）。

    QQ/微信通道（NoneBot2 8082 / 非 /api/v1/*）不受此中间件限制。
    """

    # ASGI 中间件接口
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            # 非 HTTP（如 WS）不在此限流
            return await self._app(scope, receive, send)

        from starlette.requests import Request
        request = Request(scope, receive)
        path = request.url.path

        # 只限制 /api/v1/* 路由
        if not path.startswith("/api/v1/"):
            return await self._app(scope, receive, send)

        # 豁免路径
        if path.startswith("/api/v1/health") or path.startswith("/api/v1/app/version"):
            return await self._app(scope, receive, send)

        client_ip = request.client.host if request.client else "unknown"

        # IP 级限流（全局兜底）
        if not await check_ip_rate(client_ip):
            from starlette.responses import JSONResponse
            return await JSONResponse(
                {"code": "rate_limited", "message": "请求太频繁，请稍后再试"},
                status_code=429,
            )(scope, receive, send)

        # Token 级限流
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            if not await check_token_rate(token):
                from starlette.responses import JSONResponse
                return await JSONResponse(
                    {"code": "rate_limited", "message": "Token 请求太频繁"},
                    status_code=429,
                )(scope, receive, send)

        return await self._app(scope, receive, send)

    def __init__(self, app):
        self._app = app
