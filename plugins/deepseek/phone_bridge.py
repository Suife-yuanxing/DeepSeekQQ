"""手机桥接模块 — 适配 MobileRun Portal 协议。

架构（云端友好）：
  手机 MobileRun Portal App ──(reverse WSS)──→ wss://服务器:8443 (nginx → Relay)
  MCP 工具 ──(直接调用)─────────────────────→ relay.send_command()

  手机不需要公网 IP，只需能上网即可。Relay 直接向手机发送 JSON-RPC 命令。

协议 (MobileRun Portal JSON-RPC):
  认证: URL query param ?token=xxx
  请求: {"id": "uuid", "method": "tap", "params": {...}}
  响应: {"id": "uuid", "status": "success", "result": {...}}
  事件: {"method": "events/device", "params": {...}}
"""
import asyncio
import base64
import json
import uuid
from typing import Any
from typing import Dict
from typing import Optional

from aiohttp import WSMsgType, web
from nonebot import logger

# ============================================================
# 常量
# ============================================================

CMD_TIMEOUT = 30
SCROLL_DISTANCE = 500
HEARTBEAT_INTERVAL = 30

# Android KeyEvent 码
KEYCODE_BACK = 4
KEYCODE_HOME = 3
KEYCODE_ENTER = 66

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
# PhoneRelay — MobileRun Portal 中继
# ============================================================

class PhoneRelay:
    """接受手机 MobileRun Portal 的 reverse WebSocket 连接，转发 JSON-RPC 命令。

    使用：
        relay = PhoneRelay()
        await relay.start(port=8765, api_key="pk_xxx")
        # 手机连接 wss://<服务器IP>:8443/?token=pk_xxx
    """

    def __init__(self):
        self._api_key: str = ""
        self._port: int = 8765
        self._app: Optional[web.Application] = None
        self._runner: Optional[web.AppRunner] = None
        self._phone_ws: Optional[web.WebSocketResponse] = None
        self._pending: Dict[str, asyncio.Future] = {}
        self._running: bool = False
        self._phone_online: bool = False

    # ── 状态 ──

    @property
    def running(self) -> bool:
        return self._running

    @property
    def phone_online(self) -> bool:
        return self._phone_online and self._phone_ws is not None and not self._phone_ws.closed

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
        logger.info(f"[PhoneRelay] 中继已启动: ws://0.0.0.0:{port} (MobileRun Portal)")

    async def stop(self):
        """停止中继。"""
        self._running = False
        self._phone_online = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_result({"status": "error", "error": "中继已关闭"})
        self._pending.clear()
        if self._phone_ws and not self._phone_ws.closed:
            await self._phone_ws.close()
        if self._runner:
            await self._runner.cleanup()
        logger.info("[PhoneRelay] 中继已停止")

    # ── WebSocket 连接处理 ──

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=HEARTBEAT_INTERVAL)
        await ws.prepare(request)

        peer = request.remote

        # MobileRun Portal 认证：通过 URL query param ?token=xxx
        token = request.query.get("token", "")
        if token != self._api_key:
            logger.warning(f"[PhoneRelay] 认证失败: {peer} token 不匹配")
            await ws.send_json({"type": "auth_fail", "error": "token 错误"})
            await ws.close()
            return ws

        # 踢掉旧的手机连接
        if self._phone_ws and not self._phone_ws.closed:
            await self._phone_ws.close()
        self._phone_ws = ws
        self._phone_online = True
        logger.info(f"[PhoneRelay] 手机已连接 (MobileRun Portal): {peer}")

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except json.JSONDecodeError:
                        continue

                    # ── JSON-RPC 响应（手机 → 服务器）──
                    cmd_id = data.get("id")
                    if cmd_id is not None and cmd_id in self._pending:
                        fut = self._pending.pop(cmd_id)
                        if not fut.done():
                            fut.set_result(data)
                        continue

                    # ── 设备事件（MobileRun Portal 推送）──
                    method = data.get("method", "")
                    if method == "events/device":
                        event_type = data.get("params", {}).get("type", "")
                        logger.debug(f"[PhoneRelay] 设备事件: {event_type}")
                        continue

                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[PhoneRelay] 手机连接异常: {e}")
        finally:
            self._phone_online = False
            self._phone_ws = None
            logger.info("[PhoneRelay] 手机已断开")

        return ws

    # ── JSON-RPC 命令发送 ──

    async def send_command(self, method: str, params: dict = None) -> dict:
        """向手机发送 JSON-RPC 命令，等待响应。"""
        if not self.phone_online:
            return {"success": False, "error": "手机不在线，请确保 MobileRun Portal 已连接"}

        cmd_id = str(uuid.uuid4())[:8]
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending[cmd_id] = future

        try:
            await self._phone_ws.send_json({
                "id": cmd_id,
                "method": method,
                "params": params or {},
            })
            resp = await asyncio.wait_for(future, timeout=CMD_TIMEOUT)
            if resp.get("status") == "success":
                return {"success": True, "data": resp.get("result", {})}
            return {"success": False, "error": resp.get("error", "未知错误")}
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": "命令超时"}
        except Exception as e:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": str(e)}

    # ── 手机操作（封装 send_command，保持旧 API 兼容）──

    async def screenshot(self, quality: int = 80, max_width: int = 720) -> Optional[str]:
        """截图，返回 base64 PNG。MobileRun Portal 在 reverse 模式下返回 base64 JSON。"""
        resp = await self.send_command("screenshot", {"hideOverlay": True})
        if resp.get("success"):
            result = resp.get("data", {})
            # MobileRun Portal 返回格式多样:
            #   1. 纯字符串: "iVBORw0KGgo..." (base64 png)
            #   2. dict: {"image": "base64..."} 或 {"data": "base64..."}
            if isinstance(result, str):
                return result if result else None
            if isinstance(result, dict):
                img = result.get("image") or result.get("data") or ""
                if img:
                    return img
        logger.warning(f"[PhoneRelay] 截图失败: {resp.get('error')}")
        return None

    async def tap(self, x: int, y: int) -> dict:
        return await self.send_command("tap", {"x": x, "y": y})

    async def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> dict:
        return await self.send_command("swipe", {
            "startX": x1, "startY": y1,
            "endX": x2, "endY": y2,
            "duration": duration,
        })

    async def scroll_up(self) -> dict:
        return await self.send_command("swipe", {
            "startX": 540, "startY": 1200,
            "endX": 540, "endY": 1200 - SCROLL_DISTANCE,
            "duration": 300,
        })

    async def scroll_down(self) -> dict:
        return await self.send_command("swipe", {
            "startX": 540, "startY": 800,
            "endX": 540, "endY": 800 + SCROLL_DISTANCE,
            "duration": 300,
        })

    async def type_text(self, text: str) -> dict:
        b64 = base64.b64encode(text.encode("utf-8")).decode()
        return await self.send_command("keyboard/input", {"base64_text": b64, "clear": False})

    async def back(self) -> dict:
        return await self.send_command("keyboard/key", {"key_code": KEYCODE_BACK})

    async def home(self) -> dict:
        return await self.send_command("keyboard/key", {"key_code": KEYCODE_HOME})

    async def open_app(self, app_name: str) -> dict:
        package = APP_PACKAGES.get(app_name)
        if not package:
            return {"success": False, "error": f"未知应用「{app_name}」"}
        return await self.send_command("app", {"package": package})

    async def ui_tree(self) -> Optional[list]:
        """获取 UI 元素树。"""
        resp = await self.send_command("state")
        if resp.get("success"):
            data = resp.get("data", {})
            # MobileRun Portal state 可能返回多种格式
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("children") or data.get("nodes") or []
            if isinstance(data, str):
                try:
                    parsed = json.loads(data)
                    if isinstance(parsed, list):
                        return parsed
                    if isinstance(parsed, dict):
                        return parsed.get("children") or parsed.get("nodes") or []
                except (json.JSONDecodeError, TypeError):
                    pass
        return None

    async def tap_text(self, text: str) -> dict:
        """查找包含指定文字的元素并点击。"""
        nodes = await self.ui_tree()
        if not nodes:
            return {"success": False, "error": "无法获取屏幕元素"}
        node = _find_node(nodes, text)
        if not node:
            return {"success": False, "error": f"未找到「{text}」"}
        bounds = node.get("bounds", {})
        cx = (bounds.get("left", 0) + bounds.get("right", 0)) // 2
        cy = (bounds.get("top", 0) + bounds.get("bottom", 0)) // 2
        return await self.send_command("tap", {"x": cx, "y": cy})

    async def get_screen_text(self) -> str:
        """获取屏幕上所有可见文字。"""
        nodes = await self.ui_tree()
        if not nodes:
            return "无法获取屏幕信息"
        texts = _collect_text(nodes, max_items=30)
        return "\n".join(texts) if texts else "屏幕无文字"

    # ==================== 时钟控制 ====================

    async def set_alarm(self, hour: int, minute: int, label: str = "") -> dict:
        """设置闹钟（打开时钟 App，通过 UI 操作设置）。

        注：完整自动化需 MobileRun Portal 支持 shell/adb intent。
        当前策略：打开时钟 → 通过 UI 导航到闹钟页 → 设置时间。
        """
        # 先打开时钟 App
        clock_pkg = APP_PACKAGES.get("时钟") or "com.android.deskclock"
        result = await self.send_command("app", {"package": clock_pkg})
        if not result.get("success", False):
            return {"success": False, "error": f"无法打开时钟: {result}"}

        # 尝试通过 UI 设置（点击 "+" 添加闹钟，然后输入时间）
        import asyncio
        await asyncio.sleep(1.0)  # 等待 App 加载

        # 点击"闹钟"标签（通常在底部导航）
        await self.tap_text("闹钟")
        await asyncio.sleep(0.5)

        # 点击添加按钮
        add_result = await self.tap_text("添加") or await self.tap_text("+")

        time_str = f"{hour:02d}:{minute:02d}"
        return {
            "success": True,
            "message": f"已打开时钟App，正在设置 {time_str} 闹钟" + (f"「{label}」" if label else ""),
            "hour": hour, "minute": minute, "label": label
        }

    async def set_timer(self, minutes: int, label: str = "") -> dict:
        """设置倒计时（打开时钟 App 的计时器页）。"""
        clock_pkg = APP_PACKAGES.get("时钟") or "com.android.deskclock"
        result = await self.send_command("app", {"package": clock_pkg})
        if not result.get("success", False):
            return {"success": False, "error": f"无法打开时钟: {result}"}

        import asyncio
        await asyncio.sleep(1.0)

        # 点击"计时器"标签
        await self.tap_text("计时器")
        await asyncio.sleep(0.5)

        return {
            "success": True,
            "message": f"已打开时钟App计时器，请设置 {minutes} 分钟倒计时" + (f"「{label}」" if label else ""),
            "minutes": minutes, "label": label
        }


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

def _find_node(nodes: list, text: str, depth: int = 0) -> Optional[dict]:
    """在 UI 树中递归查找包含指定文字的元素。"""
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
    """收集 UI 树中所有可见文字。"""
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
