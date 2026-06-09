"""轻量级 ScreenMCP Worker — Python 实现的 WebSocket 中转服务。

替代 Rust 版 Worker，让手机 ScreenMCP App 可以直连服务器。

架构：
  手机 ScreenMCP App (role=phone) ←WebSocket→ 本 Worker ←WebSocket→ Bot (role=controller)

协议：兼容 ScreenMCP Worker 协议
  - 手机端认证：{"type":"auth", "user_id":"...", "role":"phone", ...}
  - 控制端认证：{"type":"auth", "key":"pk_...", "role":"controller", ...}
  - 命令：{"cmd":"click", "params":{"x":100,"y":200}}
  - 响应：{"id":1, "status":"ok", "result":{...}}
  - 心跳：{"type":"ping"} → {"type":"pong"}
"""
import asyncio
import json
import time
import uuid
from typing import Dict
from typing import Optional
from typing import Set

import aiohttp
from aiohttp import WSMsgType
from aiohttp import web
from nonebot import logger

# ============================================================
# 配置
# ============================================================

WORKER_PORT = 8765  # Worker 监听端口
API_KEY = ""  # controller 认证密钥，启动时设置
AUTH_TIMEOUT = 10  # 认证超时（秒）
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）
HEARTBEAT_TIMEOUT = 60  # 心跳超时（秒）

# ============================================================
# 连接管理
# ============================================================

class Connection:
    """单个 WebSocket 连接。"""
    def __init__(self, ws: web.WebSocketResponse, role: str, device_id: str = ""):
        self.ws = ws
        self.role = role  # "phone" or "controller"
        self.device_id = device_id
        self.last_pong = time.time()
        self.cmd_id = 0

    def next_cmd_id(self) -> int:
        self.cmd_id += 1
        return self.cmd_id


class WorkerState:
    """Worker 全局状态。"""
    def __init__(self):
        self.phone: Optional[Connection] = None
        self.controllers: Set[Connection] = set()
        self.pending_cmds: Dict[int, asyncio.Future] = {}

    def is_phone_connected(self) -> bool:
        return self.phone is not None and not self.phone.ws.closed

    async def send_to_phone(self, msg: dict) -> bool:
        if not self.is_phone_connected():
            return False
        try:
            await self.phone.ws.send_json(msg)
            return True
        except Exception:
            return False


state = WorkerState()

# ============================================================
# WebSocket 处理
# ============================================================

async def handle_ws(request: web.Request) -> web.WebSocketResponse:
    """处理 WebSocket 连接。"""
    logger.info(f"[Worker] 新连接来自: {request.remote}")
    ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
    await ws.prepare(request)
    logger.info(f"[Worker] WebSocket 握手成功: {request.remote}")

    conn: Optional[Connection] = None

    try:
        # Step 1: 等待认证消息（10 秒内）
        try:
            auth_msg = await asyncio.wait_for(ws.receive_json(), timeout=AUTH_TIMEOUT)
            logger.info(f"[Worker] 收到认证消息: {auth_msg}")
        except asyncio.TimeoutError:
            logger.warning(f"[Worker] 认证超时: {request.remote}")
            await ws.send_json({"type": "auth_fail", "error": "auth timeout"})
            await ws.close()
            return ws

        # Step 2: 验证认证
        role = auth_msg.get("role", "")
        key = auth_msg.get("key", "")
        user_id = auth_msg.get("user_id", "")
        device_id = auth_msg.get("target_device_id", "") or auth_msg.get("device_id", "")

        if role == "controller":
            if not API_KEY or key != API_KEY:
                await ws.send_json({"type": "auth_fail", "error": "invalid token"})
                await ws.close()
                return ws
            conn = Connection(ws, "controller")
            state.controllers.add(conn)
            await ws.send_json({
                "type": "auth_ok",
                "resume_from": 0,
                "phone_connected": state.is_phone_connected(),
            })
            logger.info("[Worker] Controller 已连接")

        elif role == "phone":
            conn = Connection(ws, "phone", device_id)
            state.phone = conn
            await ws.send_json({"type": "auth_ok", "resume_from": 0})
            logger.info(f"[Worker] 手机已连接: {device_id}")
            # 通知所有 controller
            for ctrl in list(state.controllers):
                try:
                    await ctrl.ws.send_json({"type": "phone_status", "connected": True})
                except Exception:
                    state.controllers.discard(ctrl)

        else:
            await ws.send_json({"type": "auth_fail", "error": f"unknown role: {role}"})
            await ws.close()
            return ws

        # Step 3: 消息循环
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                await _handle_message(conn, data)
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break

    except Exception as e:
        logger.error(f"[Worker] 连接异常: {e}")
    finally:
        # 清理
        if conn:
            if conn.role == "phone" and state.phone is conn:
                state.phone = None
                for ctrl in list(state.controllers):
                    try:
                        await ctrl.ws.send_json({"type": "phone_status", "connected": False})
                    except Exception:
                        state.controllers.discard(ctrl)
                logger.info("[Worker] 手机已断开")
            elif conn.role == "controller":
                state.controllers.discard(conn)
                logger.info("[Worker] Controller 已断开")
        await ws.close()

    return ws


async def _handle_message(conn: Connection, data: dict):
    """处理收到的消息。"""
    msg_type = data.get("type", "")

    # 心跳
    if msg_type == "ping":
        await conn.ws.send_json({"type": "pong"})
        conn.last_pong = time.time()
        return

    if msg_type == "pong":
        conn.last_pong = time.time()
        return

    # Controller 发送的命令 → 转发给手机
    if "cmd" in data and conn.role == "controller":
        if not state.is_phone_connected():
            await conn.ws.send_json({
                "type": "error",
                "error": "手机未连接",
            })
            return

        cmd_id = state.phone.next_cmd_id()
        # 创建 Future 等待手机响应
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        state.pending_cmds[cmd_id] = future

        # 转发给手机
        phone_msg = {"id": cmd_id, "cmd": data["cmd"], "params": data.get("params", {})}
        await state.send_to_phone(phone_msg)

        # 通知 controller 命令已接受
        await conn.ws.send_json({"type": "cmd_accepted", "id": cmd_id})

        # 等待手机响应（30 秒超时）
        try:
            response = await asyncio.wait_for(future, timeout=30)
            await conn.ws.send_json(response)
        except asyncio.TimeoutError:
            await conn.ws.send_json({"id": cmd_id, "status": "error", "error": "命令超时"})
        finally:
            state.pending_cmds.pop(cmd_id, None)
        return

    # 手机返回的命令响应 → 转发给对应的 controller
    if "id" in data and "status" in data and conn.role == "phone":
        cmd_id = data["id"]
        future = state.pending_cmds.get(cmd_id)
        if future and not future.done():
            future.set_result(data)
        return

    logger.warning(f"[Worker] 未知消息: {data}")


# ============================================================
# 启动入口
# ============================================================

def create_app(api_key: str, port: int = WORKER_PORT) -> web.Application:
    """创建 Worker Web 应用。"""
    global API_KEY
    API_KEY = api_key

    app = web.Application()
    app.router.add_get("/", handle_ws)
    app.router.add_get("/ws", handle_ws)
    return app


async def start_worker(api_key: str, port: int = WORKER_PORT):
    """启动 Worker 服务（支持 WSS）。"""
    import ssl
    from pathlib import Path

    app = create_app(api_key, port)
    runner = web.AppRunner(app)
    await runner.setup()

    # 检查 SSL 证书
    cert_dir = Path(__file__).parent.parent.parent / "certs"
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "key.pem"

    ssl_context = None
    if cert_file.exists() and key_file.exists():
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ssl_context.load_cert_chain(str(cert_file), str(key_file))
        logger.info("[Worker] SSL 证书已加载")
    else:
        logger.warning("[Worker] 未找到 SSL 证书，使用明文 WS（Android 可能无法连接）")

    # 启动 HTTP (8765) 和 HTTPS (8766) 两个端口
    site_http = web.TCPSite(runner, "0.0.0.0", port)
    await site_http.start()
    logger.info(f"[Worker] ScreenMCP Worker 已启动，端口 {port} (WS)")

    if ssl_context:
        site_https = web.TCPSite(runner, "0.0.0.0", port + 1, ssl_context=ssl_context)
        await site_https.start()
        logger.info(f"[Worker] ScreenMCP Worker SSL 已启动，端口 {port + 1} (WSS)")

    return runner
