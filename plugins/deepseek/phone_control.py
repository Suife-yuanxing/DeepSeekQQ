"""手机远程控制模块 — 通过本地 ScreenMCP Worker 控制 Android 设备。

架构：
  QQ消息 → handler.py → phone_control.py → WebSocket → Worker(8765) → 手机 ScreenMCP App

Worker 协议（兼容 ScreenMCP）：
  认证：{"type":"auth", "key":"pk_...", "role":"controller"}
  命令：{"cmd":"click", "params":{"x":100,"y":200}}
  响应：{"id":1, "status":"ok", "result":{...}}
  心跳：{"type":"ping"} → {"type":"pong"}

安全：
  - 仅 MY_QQ 用户可触发
  - 禁止危险操作
"""
import re
import json
import time
import asyncio
from typing import Optional, Dict, Any

import aiohttp

from nonebot import logger
from .config import (
    PHONE_CONTROL_ENABLED, SCREENMCP_API_KEY,
    PHONE_CONTROL_USERS, MY_QQ,
)

# ============================================================
# Worker 连接管理
# ============================================================

WORKER_URL = "ws://127.0.0.1:8765"
WORKER_SSL_URL = "wss://127.0.0.1:8766"  # SSL 端口
CMD_TIMEOUT = 30


class WorkerClient:
    """与本地 ScreenMCP Worker 的 WebSocket 连接。

    消息分发统一由 _recv_loop 处理，send_command 只注册 Future 并等待，
    避免多处同时读取同一 WebSocket 导致竞争。
    """

    def __init__(self):
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._connected = False
        self._pending: Dict[int, asyncio.Future] = {}
        self._recv_task: Optional[asyncio.Task] = None
        self._cmd_counter: int = 0
        self._last_response: Optional[dict] = None
        self._response_event: asyncio.Event = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected and self._ws is not None and not self._ws.closed

    async def connect(self) -> bool:
        """连接到 Worker 并认证。"""
        if self.connected:
            return True
        try:
            self._session = aiohttp.ClientSession()
            # 本地连接用 WS，不验证 SSL
            self._ws = await self._session.ws_connect(WORKER_URL, timeout=10)

            # 发送认证
            await self._ws.send_json({
                "type": "auth",
                "key": SCREENMCP_API_KEY,
                "role": "controller",
                "version": {"major": 1, "minor": 0, "component": "sdk-py"},
            })

            # 等待认证响应
            resp = await asyncio.wait_for(self._ws.receive_json(), timeout=10)
            if resp.get("type") != "auth_ok":
                logger.error(f"[手机] Worker 认证失败: {resp}")
                await self.disconnect()
                return False

            self._connected = True
            # 启动后台接收任务
            self._recv_task = asyncio.create_task(self._recv_loop())
            logger.info(f"[手机] Worker 已连接，手机在线: {resp.get('phone_connected', False)}")
            return True
        except Exception as e:
            logger.error(f"[手机] Worker 连接失败: {e}")
            await self.disconnect()
            return False

    async def disconnect(self):
        """断开连接。"""
        self._connected = False
        if self._recv_task and not self._recv_task.done():
            self._recv_task.cancel()
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()
        self._ws = None
        self._session = None
        # 取消所有等待中的命令
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def _recv_loop(self):
        """后台接收消息循环 — 唯一的 WebSocket 读取点。"""
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    msg_type = data.get("type", "")

                    # 心跳
                    if msg_type == "ping":
                        await self._ws.send_json({"type": "pong"})
                        continue

                    # 命令已接受 — 记录 cmd_id 但不处理
                    if msg_type == "cmd_accepted":
                        continue

                    # 手机状态变化
                    if msg_type == "phone_status":
                        logger.info(f"[手机] 手机状态: {'在线' if data.get('connected') else '离线'}")
                        continue

                    # 错误消息
                    if msg_type == "error":
                        # 尝试匹配到等待中的命令
                        cmd_id = data.get("id")
                        if cmd_id is not None:
                            fut = self._pending.pop(cmd_id, None)
                            if fut and not fut.done():
                                fut.set_result({"success": False, "error": data.get("error", "unknown")})
                        continue

                    # 命令响应（有 status 字段）
                    if "status" in data:
                        cmd_id = data.get("id")
                        if cmd_id is not None:
                            fut = self._pending.pop(cmd_id, None)
                            if fut and not fut.done():
                                fut.set_result(data)
                        else:
                            # 无 id 的响应，用事件通知（兼容旧协议）
                            self._last_response = data
                            self._response_event.set()
                        continue

                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                    break
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[手机] 接收循环异常: {e}")
        finally:
            self._connected = False
            # 唤醒所有等待中的命令
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_result({"success": False, "error": "连接已断开"})
            self._pending.clear()
            self._response_event.set()  # 唤醒无 id 等待

    async def send_command(self, cmd: str, params: dict = None) -> dict:
        """发送命令并等待响应。所有响应由 _recv_loop 分发。"""
        if not self.connected:
            return {"success": False, "error": "未连接 Worker"}

        # 生成命令 ID，注册 Future
        self._cmd_counter += 1
        cmd_id = self._cmd_counter
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        self._pending[cmd_id] = future

        try:
            # 发送命令（带 id）
            await self._ws.send_json({"id": cmd_id, "cmd": cmd, "params": params or {}})

            # 等待 _recv_loop 通过 id 匹配设置结果
            resp = await asyncio.wait_for(future, timeout=CMD_TIMEOUT)
            return resp
        except asyncio.TimeoutError:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": "命令超时"}
        except Exception as e:
            self._pending.pop(cmd_id, None)
            return {"success": False, "error": str(e)}


# 全局客户端实例
_client = WorkerClient()


async def _ensure_connected() -> bool:
    """确保 Worker 连接。"""
    if _client.connected:
        return True
    return await _client.connect()


async def _call_worker(cmd: str, params: dict = None) -> dict:
    """调用 Worker 命令，返回标准化结果。"""
    if not await _ensure_connected():
        return {"success": False, "error": "无法连接 Worker，检查 bot 是否启动"}

    resp = await _client.send_command(cmd, params)

    # 标准化响应
    if resp.get("status") == "ok":
        return {"success": True, "data": resp.get("result", {})}
    elif resp.get("status") == "error":
        return {"success": False, "error": resp.get("error", "未知错误")}
    elif resp.get("success") is not None:
        return resp  # 已经是标准格式
    else:
        return {"success": True, "data": resp}


# ============================================================
# 常用应用包名映射
# ============================================================

APP_MAP: Dict[str, str] = {
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
    "电话": "com.android.dialer",
    "短信": "com.android.mms",
    "钉钉": "com.alibaba.android.rimet",
    "飞书": "com.ss.android.lark",
    "知乎": "com.zhihu.android",
    "微博": "com.sina.weibo",
    "拼多多": "com.xunmeng.pinduoduo",
}

# ============================================================
# 指令映射
# ============================================================

DIRECT_COMMANDS: Dict[str, tuple] = {
    "返回": ("back", {}),
    "回退": ("back", {}),
    "后退": ("back", {}),
    "回到桌面": ("home", {}),
    "回到主页": ("home", {}),
    "最近任务": ("recents", {}),
    "上滑": ("scroll", {"x": 540, "y": 1200, "dx": 0, "dy": -500}),
    "下滑": ("scroll", {"x": 540, "y": 800, "dx": 0, "dy": 500}),
    "左滑": ("scroll", {"x": 800, "y": 1000, "dx": -500, "dy": 0}),
    "右滑": ("scroll", {"x": 300, "y": 1000, "dx": 500, "dy": 0}),
    "往上滑": ("scroll", {"x": 540, "y": 1200, "dx": 0, "dy": -500}),
    "往下滑": ("scroll", {"x": 540, "y": 800, "dx": 0, "dy": 500}),
    "截屏": ("screenshot", {}),
    "截图": ("screenshot", {}),
    "截个图": ("screenshot", {}),
    "截个屏": ("screenshot", {}),
    "屏幕状态": ("ui_tree", {}),
    "当前界面": ("ui_tree", {}),
}


# ============================================================
# 高级操作封装
# ============================================================

async def phone_tap(x: int, y: int) -> str:
    resp = await _call_worker("click", {"x": x, "y": y})
    return _format_result("点击", resp, f"({x}, {y})")


async def phone_type(text: str) -> str:
    resp = await _call_worker("type", {"text": text})
    return _format_result("输入文字", resp, text[:30])


async def phone_screenshot() -> Optional[str]:
    resp = await _call_worker("screenshot", {"quality": 80, "max_width": 720})
    if resp.get("success") and resp.get("data", {}).get("image"):
        return resp["data"]["image"]
    return None


async def phone_get_screen_text() -> str:
    resp = await _call_worker("ui_tree")
    if not resp.get("success"):
        return "无法获取屏幕信息"
    data = resp.get("data", {})
    if isinstance(data, dict) and "text" in data:
        return data["text"][:1000]
    return str(data)[:500]


def _format_result(action: str, resp: dict, detail: str = "") -> str:
    if resp.get("success"):
        return f"📱 {action}成功" + (f"：{detail}" if detail else "")
    return f"📱 {action}失败：{resp.get('error', '未知错误')}"


# ============================================================
# 指令解析
# ============================================================

def parse_phone_command(raw_msg: str) -> Optional[Dict[str, Any]]:
    msg = raw_msg.strip()

    m = re.search(r"(?:往上滑|上滑)\s*(\d+)\s*[下次]", msg)
    if m:
        count = min(int(m.group(1)), 10)
        return {"action": "scroll_multi", "params": {"direction": "up", "count": count}, "description": f"上滑{count}次"}

    m = re.search(r"(?:往下滑|下滑)\s*(\d+)\s*[下次]", msg)
    if m:
        count = min(int(m.group(1)), 10)
        return {"action": "scroll_multi", "params": {"direction": "down", "count": count}, "description": f"下滑{count}次"}

    m = re.search(r"打开\s*(.+?)(?:\s*$|\s*[吧呢啊])", msg)
    if m:
        app_name = m.group(1).strip()
        return {"action": "open_app", "params": {"app_name": app_name}, "description": f"打开{app_name}"}

    m = re.search(r"点击\s*[「「\"]?(.+?)[」」\"]?\s*$", msg)
    if m:
        target = m.group(1).strip()
        coord_m = re.match(r"(\d+)\s*[,，]\s*(\d+)", target)
        if coord_m:
            x, y = int(coord_m.group(1)), int(coord_m.group(2))
            return {"action": "tap", "params": {"x": x, "y": y}, "description": f"点击({x},{y})"}
        return {"action": "click_element", "params": {"text": target}, "description": f"点击「{target}」"}

    m = re.search(r"(?:输入|打字|输入文字)\s*[：:]?\s*(.+)", msg)
    if m:
        text = m.group(1).strip()
        return {"action": "type_text", "params": {"text": text}, "description": f"输入「{text}」"}

    for keyword, (tool, args) in DIRECT_COMMANDS.items():
        if keyword in msg:
            return {"action": tool, "params": args, "description": keyword}

    return None


def is_phone_command(raw_msg: str) -> bool:
    if not PHONE_CONTROL_ENABLED:
        return False
    phone_keywords = [
        "手机", "打开", "截屏", "截图", "截个屏", "截个图",
        "点击", "滑动", "返回桌面", "回到桌面",
        "上滑", "下滑", "左滑", "右滑", "锁屏", "输入", "打字",
    ]
    return any(kw in raw_msg for kw in phone_keywords)


# ============================================================
# 指令执行器
# ============================================================

async def execute_phone_command(raw_msg: str) -> Optional[str]:
    if not is_phone_command(raw_msg):
        return None

    if not SCREENMCP_API_KEY:
        return "📱 未配置 SCREENMCP_API_KEY"

    cmd = parse_phone_command(raw_msg)
    if not cmd:
        return None

    action = cmd["action"]
    params = cmd["params"]
    desc = cmd["description"]

    logger.info(f"[手机] 执行: {desc} ({action})")

    if action == "screenshot":
        img_b64 = await phone_screenshot()
        if img_b64:
            return f"[CQ:image,file=base64://{img_b64}]"
        return "📱 截图失败"

    elif action == "ui_tree":
        text = await phone_get_screen_text()
        return f"📱 当前屏幕：\n{text[:500]}"

    elif action == "scroll_multi":
        count = params.get("count", 1)
        direction = params.get("direction", "up")
        dy = -500 if direction == "up" else 500
        for i in range(count):
            await _call_worker("scroll", {"x": 540, "y": 1000, "dx": 0, "dy": dy})
            if i < count - 1:
                await asyncio.sleep(0.5)
        return f"📱 {direction}滑{count}次完成"

    elif action == "open_app":
        return "📱 打开应用功能需要在手机上手动操作（ScreenMCP 不支持直接打开应用）"

    elif action == "tap":
        return await phone_tap(params["x"], params["y"])

    elif action == "click_element":
        text = params["text"]
        resp = await _call_worker("ui_tree")
        if resp.get("success"):
            data = resp.get("data", {})
            nodes = data if isinstance(data, list) else []
            target = _find_node(nodes, text)
            if target:
                bounds = target.get("bounds", {})
                cx = (bounds.get("left", 0) + bounds.get("right", 0)) // 2
                cy = (bounds.get("top", 0) + bounds.get("bottom", 0)) // 2
                return await phone_tap(cx, cy)
        return f"📱 未找到「{text}」"

    elif action == "type_text":
        return await phone_type(params["text"])

    elif action == "back":
        resp = await _call_worker("back")
        return _format_result("返回", resp)

    elif action == "home":
        resp = await _call_worker("home")
        return _format_result("桌面", resp)

    elif action == "recents":
        resp = await _call_worker("recents")
        return _format_result("最近任务", resp)

    elif action == "scroll":
        resp = await _call_worker("scroll", params)
        return _format_result("滚动", resp)

    else:
        return f"📱 未支持的操作：{action}"


def _find_node(nodes: list, text: str, depth: int = 0) -> Optional[dict]:
    if depth > 10:
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
