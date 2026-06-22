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
from fastapi.staticfiles import StaticFiles

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

# ── 静态托管前端原型（APP 落地：浏览器调试 + 非 APK 访问）──
# 必须在所有 include_router 之后，否则会吞掉 /api/v1/* 路由。
# Capacitor 打包时 HTML 进 APK 本地加载，不走这里；这里供浏览器直接访问 8766 调试。
# 路径相对 WorkingDirectory（双层 DeepSeekQQ/DeepSeekQQ）：../../安卓控制面板UI原型
_PROTOTYPE_DIR = os.getenv("PLATFORM_PROTOTYPE_DIR", "../../安卓控制面板UI原型")
if os.path.isdir(_PROTOTYPE_DIR):
    app.mount("/", StaticFiles(directory=_PROTOTYPE_DIR, html=True), name="prototype")
else:
    @app.get("/")
    async def _root():
        return {"ok": True, "version": "1.0.0", "note": "前端原型目录未配置，仅 API 可用"}


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
