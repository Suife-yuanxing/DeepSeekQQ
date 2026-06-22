"""FastAPI 用户侧 API 服务器 — 端口 8766。

与现有 NoneBot2 8082 进程物理隔离（S6）。
启动：python -m plugins.deepseek.api_platform.server  或  uvicorn plugins.deepseek.api_platform.server:app --port 8766

Task 1.1: 骨架 + /api/v1/health
注册路由：auth + bots + chat（P0 三件套）
"""
import os
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth
from . import bots
from . import chat
from . import chat_media
from . import templates
from . import abilities
from . import quota
from . import dashboard
from . import notifications
from . import admin
from . import stats
from . import channels
from . import api_keys
from ..db_platform import init_platform_tables

app = FastAPI(
    title="林念念 Bot 控制面板 API",
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

# CORS（开发期允许所有，生产收紧）
_cors = os.getenv("PLATFORM_CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _cors.split(",")] if _cors != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup() -> None:
    """启动时初始化多租户表（幂等）。"""
    # db_core.get_db 需要 nonebot 配置，这里用独立路径
    from ..db_core import get_db
    await init_platform_tables()


@app.get("/api/v1/health")
async def health():
    """Task 1.1: 健康检查 + 版本协商。"""
    try:
        from ..db_core import get_db
        db = await get_db()
        async with db.execute("SELECT 1") as cur:
            await cur.fetchone()
        db_status = "connected"
    except Exception as e:
        db_status = f"error: {e}"
    return {
        "ok": db_status == "connected",
        "version": "1.0.0",
        "min_app_version": "1.0.0",
        "db": db_status,
    }


# 注册路由
app.include_router(auth.router)
app.include_router(bots.router)
app.include_router(chat.router)
app.include_router(chat_media.router)
app.include_router(templates.router)
app.include_router(abilities.router)
app.include_router(quota.router)
app.include_router(dashboard.router)
app.include_router(notifications.router)
app.include_router(admin.router)
app.include_router(stats.router)
app.include_router(channels.router)
app.include_router(api_keys.router)


def main() -> None:
    """直接运行入口。"""
    import uvicorn
    port = int(os.getenv("PLATFORM_API_PORT", "8766"))
    host = os.getenv("PLATFORM_API_HOST", "127.0.0.1")
    uvicorn.run(
        "plugins.deepseek.api_platform.server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
