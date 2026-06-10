"""手机桥接模块 — WebSocket 中继，让手机主动连出到服务器。

架构（云端友好）：
  手机 ScreenMCP App ──(outbound WS)──→ ws://服务器公网IP:8765 (Relay)
  Bot MCP 工具 ──(local WS)──────────→ ws://127.0.0.1:8765    (Relay)

  手机不需要公网 IP，只需能上网即可。Relay 负责转发命令和响应。

协议：
  手机端认证：{"type":"auth", "key":"pk_...", "role":"phone"}
  控制端认证：{"type":"auth", "key":"pk_...", "role":"controller"}
  命令：{"id":1, "cmd":"click", "params":{"x":100,"y":200}}
  响应：{"id":1, "status":"ok", "result":{...}}
  心跳：{"type":"ping"} → {"type":"pong"}
"""
import asyncio
import json
import logging
import time
from typing import Any
from typing import Dict
from typing import Optional

import aiohttp
from aiohttp import WSMsgType, web

logger = logging.getLogger("deepseek.phone_bridge")

# ============================================================
# 常量
# ============================================================

CMD_TIMEOUT = 30
MAX_COORD = 9999
SCROLL_DISTANCE = 500
HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 60

APP_PACKAGES: Dict[str, str] = {
    "微信": "com.tencent.mm",
    "QQ": "com.tencent.mobileqq",
    "抖音": "com.ss.android.ugc.aweme",
    "快手": "com.smile.gifmaker",
    "支付宝": "com.eg.android.AlipayGphone",
    "淘宝": "com.taobao.taobao",
    "京东": "com.jingdong.app.mall",
    "B站": "tv.danmaku.bili",
    "哔哩哔哩": "tv.danmaku.bili",
    "小红书": "com.xingin.xhs",
    "美团": "com.sankuai.meituan",
    "饿了么": "me.ele",
    "设置": "com.android.settings",
    "相机": "com.android.camera",
    "钉钉": "com.alibaba.android.rimet",
    "飞书": "com.ss.android.lark",
    "知乎": "com.zhihu.android",
    "微博": "com.sina.weibo",
    "拼多多": "com.xunmeng.pinduoduo",
}


# ============================================================
# PhoneRelay — WebSocket 中继服务器
# ============================================================

class PhoneRelay:
    """WebSocket 中继：接受手机和控制端的连接，转发消息。

    使用：
        relay = PhoneRelay()
        await relay.start(port=8765, api_key="pk_xxx")
        # 手机连接 ws://server:8765，控制端连接 ws://127.0.0.1:8765
    """

    def __init__(self):
        self._api_key: str = ""
        self._port: int = 8765
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._phone_ws: Optional[web.WebSocketResponse] = None
        self._controller_ws: Optional[web.WebSocketResponse] = None
        self._pending: Dict[int, asyncio.Future] = {}
        self._cmd_counter: int = 0
        self._running: bool = False
        self._phone_online: bool = False

    # ── 状态 ──

    @property
    def running(self) -> bool:
        return self._running

    @property
    def phone_online(self) -> bool:
        return self._phone_online and self._phone_ws is not None and not self._phone_ws.closed

    @property
    def controller_connected(self) -> bool:
        return self._controller_ws is not None and not self._controller_ws.closed

    # ── 启动/停止 ──

    async def start(self, port: int, api_key: str):
        """启动中继服务器。"""
        self._port = port
        self._api_key = api_key
        self._app = web.Application()
        self._app.router.add_get("/", self._handle_ws)
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", port)
        await site.start()
        self._running = True
        logger.info(f"[PhoneRelay] 中继已启动: ws://0.0.0.0:{port}")

    async def stop(self):
        """停止中继。"""
        self._running = False
        self._phone_online = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"success": False, "error": "中继已关闭"})
        self._pending.clear()
        if self._phone_ws and not self._phone_ws.closed:
            await self._phone_ws.close()
        if self._controller_ws and not self._controller_ws.closed:
            await self._controller_ws.close()
        if self._runner:
            await self._runner.cleanup()
        logger.info("[PhoneRelay] 中继已停止")

    # ── WebSocket 连接处理 ──

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
        await ws.prepare(request)

        peer = request.remote
        logger.info(f"[PhoneRelay] 新连接: {peer}")

        role = None
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "error", "error": "无效 JSON"})
                        continue

                    msg_type = data.get("type", "")

                    # ── 认证 ──
                    if msg_type == "auth":
                        key = data.get("key", "")
                        conn_role = data.get("role", "")

                        if key != self._api_key:
                            await ws.send_json({"type": "auth_fail", "error": "密钥错误"})
                            logger.warning(f"[PhoneRelay] 认证失败: {peer} role={conn_role}")
                            await ws.close()
                            return ws

                        if conn_role == "phone":
                            # 踢掉旧的手机连接
                            if self._phone_ws and not self._phone_ws.closed:
                                await self._phone_ws.close()
                            self._phone_ws = ws
                            self._phone_online = True
                            role = "phone"
                            await ws.send_json({"type": "auth_ok", "phone_connected": True})
                            logger.info(f"[PhoneRelay] 手机已认证: {peer}")

                        elif conn_role == "controller":
                            # 踢掉旧的控制端
                            if self._controller_ws and not self._controller_ws.closed:
                                await self._controller_ws.close()
                            self._controller_ws = ws
                            role = "controller"
                            await ws.send_json({
                                "type": "auth_ok",
                                "phone_connected": self.phone_online,
                            })
                            logger.info(f"[PhoneRelay] 控制端已认证: {peer}")

                        else:
                            await ws.send_json({"type": "auth_fail", "error": f"未知角色: {conn_role}"})
                            await ws.close()
                            return ws
                        continue

                    # ── 心跳 ──
                    if msg_type == "ping":
                        await ws.send_json({"type": "pong"})
                        continue

                    if msg_type == "pong":
                        continue

                    # ── 命令响应（手机 → 控制端）──
                    if role == "phone" and "status" in data:
                        cmd_id = data.get("id")
                        if cmd_id is not None and cmd_id in self._pending:
                            fut = self._pending.pop(cmd_id)
                            if not fut.done():
                                fut.set_result(data)
                        continue

                    # ── 手机状态通知 ──
                    if role == "phone" and msg_type == "phone_status":
                        self._phone_online = data.get("connected", False)
                        continue

                    # ── 命令（控制端 → 手机）──
                    if role == "controller" and "cmd" in data:
                        if not self.phone_online:
                            await ws.send_json({
                                "id": data.get("id", 0),
                                "status": "error",
                                "error": "手机不在线",
                            })
                            continue
                        # 转发到手机
                        try:
                            if self._phone_ws and not self._phone_ws.closed:
                                await self._phone_ws.send_json(data)
                            else:
                                await ws.send_json({
                                    "id": data.get("id", 0),
                                    "status": "error",
                                    "error": "手机连接已断开",
                                })
                        except Exception as e:
                            logger.error(f"[PhoneRelay] 转发命令失败: {e}")
                        continue

                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PhoneRelay] 连接异常 ({role}): {e}")
        finally:
            if role == "phone":
                self._phone_online = False
                self._phone_ws = None
                logger.info("[PhoneRelay] 手机已断开")
            elif role == "controller":
                self._controller_ws = None
                logger.info("[PhoneRelay] 控制端已断开")

        return ws

    # ── 控制端 API（供 MCP 工具调用）──

    async def send_command(self, cmd: str, params: dict = None) -> dict:
        """从控制端发送命令到手机，等待响应。"""
        if not self.phone_online:
            return {"success": False, "error": "手机不在线"}

        if not self._controller_ws or self._controller_ws.closed:
            return {"success": False, "error": "中继控制端未连接，请先调用 connect_controller()"}

        self._cmd_counter += 1
        cmd_id = self._cmd_counter
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[cmd_id] = future

        try:
            await self._controller_ws.send_json({
                "id": cmd_id, "cmd": cmd, "params": params or {},
            })
            resp = await asyncio.wait_for(future, timeout=CMD_TIMEOUT)
            if resp.get("status") == "ok":
                return {"success": True, "data": resp.get("result", {})}
            return {"success": False, "error": resp.get("error", "未知错误")}
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": "命令超时"}
        except Exception as e:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": str(e)}

    async def connect_controller(self, ws_url: str = "ws://127.0.0.1:8765") -> bool:
        """Bot 内部以 WebSocket 客户端连接到自己的中继（以 controller 角色）。"""
        if self.controller_connected:
            return True

        try:
            session = aiohttp.ClientSession()
            ws = await session.ws_connect(ws_url, timeout=10)

            await ws.send_json({
                "type": "auth",
                "key": self._api_key,
                "role": "controller",
                "version": {"major": 1, "minor": 0, "component": "sdk-py"},
            })

            resp = await asyncio.wait_for(ws.receive_json(), timeout=10)
            if resp.get("type") != "auth_ok":
                logger.error(f"[PhoneRelay] 控制端认证失败: {resp}")
                await ws.close()
                await session.close()
                return False

            self._controller_ws = ws
            # 将 session 挂到 ws 上防止被 GC
            self._controller_ws._session = session
            logger.info("[PhoneRelay] 控制端已连接到本地中继")
            return True

        except Exception as e:
            logger.error(f"[PhoneRelay] 控制端连接失败: {e}")
            return False

    # ── 手机操作（封装 send_command）──

    async def screenshot(self, quality: int = 80, max_width: int = 720) -> Optional[str]:
        resp = await self.send_command("screenshot", {"quality": quality, "max_width": max_width})
        if resp.get("success"):
            return resp.get("data", {}).get("image")
        logger.warning(f"[PhoneRelay] 截图失败: {resp.get('error')}")
        return None

    async def tap(self, x: int, y: int) -> dict:
        if not _valid_coord(x, y):
            return {"success": False, "error": f"坐标越界 ({x},{y})"}
        return await self.send_command("click", {"x": x, "y": y})

    async def swipe(self, x1: int, y1: int, x2: int, y2: int) -> dict:
        if not _valid_coord(x1, y1) or not _valid_coord(x2, y2):
            return {"success": False, "error": "坐标越界"}
        return await self.send_command("scroll", {
            "x": x1, "y": y1, "dx": x2 - x1, "dy": y2 - y1,
        })

    async def scroll_up(self) -> dict:
        return await self.send_command("scroll", {
            "x": 540, "y": 1200, "dx": 0, "dy": -SCROLL_DISTANCE,
        })

    async def scroll_down(self) -> dict:
        return await self.send_command("scroll", {
            "x": 540, "y": 800, "dx": 0, "dy": SCROLL_DISTANCE,
        })

    async def type_text(self, text: str) -> dict:
        return await self.send_command("type", {"text": text})

    async def back(self) -> dict:
        return await self.send_command("back")

    async def home(self) -> dict:
        return await self.send_command("home")

    async def open_app(self, app_name: str) -> dict:
        package = APP_PACKAGES.get(app_name)
        if not package:
            return {"success": False, "error": f"未知应用「{app_name}」"}
        return await self.send_command("open_app", {"package": package})

    async def ui_tree(self) -> Optional[list]:
        resp = await self.send_command("ui_tree")
        if resp.get("success"):
            data = resp.get("data", {})
            return data if isinstance(data, list) else data.get("children", [])
        return None

    async def tap_text(self, text: str) -> dict:
        nodes = await self.ui_tree()
        if not nodes:
            return {"success": False, "error": "无法获取屏幕元素"}
        node = _find_node(nodes, text)
        if not node:
            return {"success": False, "error": f"未找到「{text}」"}
        bounds = node.get("bounds", {})
        cx = (bounds.get("left", 0) + bounds.get("right", 0)) // 2
        cy = (bounds.get("top", 0) + bounds.get("bottom", 0)) // 2
        return await self.send_command("click", {"x": cx, "y": cy})

    async def get_screen_text(self) -> str:
        nodes = await self.ui_tree()
        if not nodes:
            return "无法获取屏幕信息"
        texts = _collect_text(nodes, max_items=30)
        return "\n".join(texts) if texts else "屏幕无文字"


# ============================================================
# 全局实例
# ============================================================

_relay = PhoneRelay()


def get_relay() -> PhoneRelay:
    """获取全局 PhoneRelay 实例。"""
    return _relay


# ============================================================
# 辅助
# ============================================================

def _valid_coord(x: int, y: int) -> bool:
    return 0 <= x <= MAX_COORD and 0 <= y <= MAX_COORD


def _find_node(nodes: list, text: str, depth: int = 0) -> Optional[dict]:
    if depth > 12:
        return None
    for node in nodes:
        if not isinstance(node, dict):
            continue
        t = node.get("text", "") or node.get("contentDesc", "") or node.get("description", "")
        if t and text in str(t):
            return node
        children = node.get("children", [])
        if isinstance(children, list):
            found = _find_node(children, text, depth + 1)
            if found:
                return found
    return None


def _collect_text(nodes: list, max_items: int = 30) -> list:
    result = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        t = node.get("text", "") or node.get("contentDesc", "")
        if t and t.strip():
            result.append(t.strip()[:80])
            if len(result) >= max_items:
                return result
        children = node.get("children", [])
        if isinstance(children, list):
            result.extend(_collect_text(children, max_items - len(result)))
    return result
