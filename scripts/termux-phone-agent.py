#!/usr/bin/env python3
"""Termux 手机控制代理 — 在手机 Termux 中运行，连接服务器 Worker 执行 ADB 命令。

使用前提：
1. 手机安装 Termux（F-Droid 版本，不要用 Play Store 版）
2. Termux 中安装: pkg install python adb
3. 手机开启 USB 调试（开发者选项）
4. 通过 ADB 授权本机调试（首次需要）

运行：
  python termux-phone-agent.py --server wss://你的服务器IP:8766 --key pk_xxx

功能：
  - 点击/滑动/输入
  - 截图
  - 获取屏幕元素
  - 打开应用
  - 返回/桌面/最近任务
"""
import json
import re
import asyncio
import subprocess
import base64
import os
import sys
import time
from typing import Optional, Dict, Any

try:
    import websockets
except ImportError:
    print("❌ 请先安装 websockets: pip install websockets")
    sys.exit(1)

# ============================================================
# 配置
# ============================================================

SERVER_URL = "wss://your-server:8766"  # 服务器地址
API_KEY = "pk_xxx"  # 认证密钥
DEVICE_ID = "termux-phone"  # 设备标识
RECONNECT_DELAY = 5  # 重连延迟（秒）
HEARTBEAT_INTERVAL = 30  # 心跳间隔（秒）

# ============================================================
# ADB 命令封装
# ============================================================

def run_adb(args: list[str], timeout: int = 30) -> tuple[bool, str]:
    """执行 ADB 命令，返回 (成功, 输出)。"""
    cmd = ["adb"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip() or "命令执行失败"
    except subprocess.TimeoutError:
        return False, "命令超时"
    except FileNotFoundError:
        return False, "未找到 adb，请安装: pkg install adb"
    except Exception as e:
        return False, str(e)


def setup_local_adb() -> bool:
    """设置本地 ADB 连接（Android 11+ 无线调试）。"""
    print("🔍 检查本地 ADB 连接...")

    # 检查是否有设备已连接
    ok, out = run_adb(["devices"])
    if ok and "device" in out and "offline" not in out:
        # 已经有设备连接
        devices = [line for line in out.split("\n") if "\tdevice" in line]
        if devices:
            print(f"✅ ADB 已连接: {devices[0].split('\t')[0]}")
            return True

    # 尝试连接本地 ADB 服务（Android 11+ 无线调试）
    print("📱 尝试连接本地 ADB 服务...")

    # 查找本地 ADB 服务端口
    # Android 11+ 无线调试端口范围不固定，先用短超时快速扫描常见区间
    try:
        import concurrent.futures

        def try_connect(port: int) -> Optional[int]:
            ok, out = run_adb(["connect", f"localhost:{port}"], timeout=1)
            if ok and "connected" in out.lower():
                return port
            return None

        # 缩小范围 + 并行探测，避免单线程扫描 3000 个端口耗时过长
        ports = list(range(37000, 37100)) + list(range(38000, 38100)) + list(range(39000, 39100))
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as pool:
            futures = {pool.submit(try_connect, p): p for p in ports}
            for fut in concurrent.futures.as_completed(futures, timeout=30):
                result = fut.result()
                if result is not None:
                    print(f"✅ 已连接到本地 ADB 服务: localhost:{result}")
                    # 取消剩余任务
                    for f in futures:
                        f.cancel()
                    return True
    except Exception:
        pass

    print("⚠️ 未找到本地 ADB 服务")
    print()
    print("请确保:")
    print("1. 手机已开启「开发者选项」")
    print("2. 已开启「USB 调试」")
    print("3. Android 11+ 需开启「无线调试」")
    print()
    print("手动连接方法:")
    print("  1. 设置 → 开发者选项 → 无线调试 → 开启")
    print("  2. 点击「使用配对码配对设备」")
    print("  3. 记下配对码和端口")
    print("  4. 运行: adb pair localhost:<端口> <配对码>")
    print("  5. 运行: adb connect localhost:<端口>")
    print()

    return False


def cmd_click(x: int, y: int) -> Dict[str, Any]:
    """点击屏幕坐标。"""
    ok, out = run_adb(["shell", "input", "tap", str(x), str(y)])
    return {"status": "ok" if ok else "error", "result": {"action": "click", "x": x, "y": y}}


def cmd_long_press(x: int, y: int, duration: int = 500) -> Dict[str, Any]:
    """长按屏幕坐标。"""
    ok, out = run_adb(["shell", "input", "swipe", str(x), str(y), str(x), str(y), str(duration)])
    return {"status": "ok" if ok else "error", "result": {"action": "long_press", "x": x, "y": y}}


def cmd_swipe(x1: int, y1: int, x2: int, y2: int, duration: int = 300) -> Dict[str, Any]:
    """滑动操作。"""
    ok, out = run_adb(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration)])
    return {"status": "ok" if ok else "error", "result": {"action": "swipe"}}


def cmd_scroll(x: int, y: int, dx: int, dy: int) -> Dict[str, Any]:
    """滚动操作（转换为 swipe）。"""
    return cmd_swipe(x, y, x + dx, y + dy, 300)


def cmd_type(text: str) -> Dict[str, Any]:
    """输入文字（需要先聚焦输入框）。"""
    # ADB input text: 空格用 %s 代替，过滤危险字符
    safe = re.sub(r"[^a-zA-Z0-9一-龥.,;:!?@#%_+=\-/]", "", text)
    escaped = safe.replace(" ", "%s")
    if not escaped:
        return {"status": "error", "error": "无有效文字"}
    ok, out = run_adb(["shell", "input", "text", escaped])
    return {"status": "ok" if ok else "error", "result": {"action": "type", "text": text[:50]}}


def cmd_keyevent(keycode: str) -> Dict[str, Any]:
    """发送按键事件。"""
    # 常用按键映射
    key_map = {
        "back": "4",
        "home": "3",
        "recents": "187",
        "power": "26",
        "volume_up": "24",
        "volume_down": "25",
        "enter": "66",
        "delete": "67",
        "tab": "61",
    }
    code = key_map.get(keycode, keycode)
    ok, out = run_adb(["shell", "input", "keyevent", code])
    return {"status": "ok" if ok else "error", "result": {"action": "keyevent", "key": keycode}}


def cmd_screenshot(quality: int = 80, max_width: int = 720) -> Dict[str, Any]:
    """截图并返回 base64。"""
    # 使用 Termux 的 TMPDIR（兼容 Android）
    tmp_dir = os.environ.get("TMPDIR", "/data/data/com.termux/files/usr/tmp")
    if not os.path.isdir(tmp_dir):
        tmp_dir = "/tmp"
    local_path = os.path.join(tmp_dir, f"screenshot_{os.getpid()}.png")
    device_path = f"/sdcard/screenshot_{os.getpid()}.png"

    try:
        ok, _ = run_adb(["shell", "screencap", "-p", device_path])
        if not ok:
            return {"status": "error", "error": "截图失败"}

        ok, _ = run_adb(["pull", device_path, local_path])
        if not ok:
            return {"status": "error", "error": "拉取截图失败"}

        # 压缩图片（如果有 PIL）
        try:
            from PIL import Image
            import io
            img = Image.open(local_path)
            if img.width > max_width:
                ratio = max_width / img.width
                new_size = (max_width, int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=quality)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except ImportError:
            with open(local_path, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()

        return {"status": "ok", "result": {"image": b64}}
    except Exception as e:
        return {"status": "error", "error": str(e)}
    finally:
        # 确保清理临时文件
        try:
            if os.path.exists(local_path):
                os.remove(local_path)
        except OSError:
            pass
        run_adb(["shell", "rm", device_path])


def cmd_ui_tree() -> Dict[str, Any]:
    """获取屏幕 UI 元素树。"""
    try:
        # 使用 uiautomator dump
        ok, _ = run_adb(["shell", "uiautomator", "dump", "/sdcard/ui.xml"])
        if not ok:
            return {"status": "error", "error": "UI dump 失败"}

        # 拉取 XML
        ok, xml_content = run_adb(["shell", "cat", "/sdcard/ui.xml"])
        run_adb(["shell", "rm", "/sdcard/ui.xml"])

        if not ok:
            return {"status": "error", "error": "读取 UI XML 失败"}

        # 简单解析 XML 提取文本
        texts = re.findall(r'text="([^"]*)"', xml_content)
        texts = [t for t in texts if t.strip()]

        return {"status": "ok", "result": {"text": "\n".join(texts[:50]), "raw_xml": xml_content[:2000]}}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def cmd_open_app(package: str) -> Dict[str, Any]:
    """打开应用。"""
    # 校验包名格式，防止注入
    if not re.match(r'^[a-zA-Z0-9._]+$', package):
        return {"status": "error", "error": f"非法包名: {package}"}
    ok, out = run_adb(["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"])
    return {"status": "ok" if ok else "error", "result": {"action": "open_app", "package": package}}


def cmd_get_current_app() -> Dict[str, Any]:
    """获取当前前台应用包名。"""
    # 注意：subprocess list 参数不支持管道，需在 Python 端过滤
    ok, out = run_adb(["shell", "dumpsys", "activity", "recents"])
    if ok and out:
        match = re.search(r'realActivity=([^/]+)', out)
        if match:
            return {"status": "ok", "result": {"package": match.group(1)}}
    return {"status": "ok", "result": {"package": "unknown"}}


# ============================================================
# 命令分发
# ============================================================

def execute_command(cmd: str, params: dict) -> Dict[str, Any]:
    """执行命令并返回结果。"""
    try:
        if cmd == "click":
            return cmd_click(params.get("x", 0), params.get("y", 0))
        elif cmd == "long_press":
            return cmd_long_press(params.get("x", 0), params.get("y", 0), params.get("duration", 500))
        elif cmd == "swipe":
            return cmd_swipe(
                params.get("x1", 0), params.get("y1", 0),
                params.get("x2", 0), params.get("y2", 0),
                params.get("duration", 300)
            )
        elif cmd == "scroll":
            return cmd_scroll(params.get("x", 0), params.get("y", 0), params.get("dx", 0), params.get("dy", 0))
        elif cmd == "type":
            return cmd_type(params.get("text", ""))
        elif cmd == "keyevent":
            return cmd_keyevent(params.get("key", ""))
        elif cmd == "back":
            return cmd_keyevent("back")
        elif cmd == "home":
            return cmd_keyevent("home")
        elif cmd == "recents":
            return cmd_keyevent("recents")
        elif cmd == "screenshot":
            return cmd_screenshot(params.get("quality", 80), params.get("max_width", 720))
        elif cmd == "ui_tree":
            return cmd_ui_tree()
        elif cmd == "open_app":
            return cmd_open_app(params.get("package", ""))
        elif cmd == "current_app":
            return cmd_get_current_app()
        else:
            return {"status": "error", "error": f"未知命令: {cmd}"}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ============================================================
# WebSocket 客户端
# ============================================================

async def connect_to_server(server_url: str, api_key: str, device_id: str):
    """连接到服务器 Worker 并处理命令。"""
    print(f"🔌 连接服务器: {server_url}")

    try:
        # 禁用 SSL 证书验证（自签名证书）
        import ssl
        ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(server_url, ping_interval=None, ssl=ssl_context) as ws:
            # 认证
            auth_msg = {
                "type": "auth",
                "role": "phone",
                "key": api_key,
                "device_id": device_id,
            }
            await ws.send(json.dumps(auth_msg))

            # 等待认证响应
            resp = await asyncio.wait_for(ws.recv(), timeout=10)
            data = json.loads(resp)

            if data.get("type") != "auth_ok":
                print(f"❌ 认证失败: {data.get('error', '未知错误')}")
                return False

            print(f"✅ 已连接到服务器，等待命令...")

            # 消息循环
            last_ping = time.time()

            while True:
                try:
                    # 接收消息（带超时，用于心跳）
                    msg = await asyncio.wait_for(ws.recv(), timeout=HEARTBEAT_INTERVAL)
                    data = json.loads(msg)

                    # 心跳
                    if data.get("type") == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        last_ping = time.time()
                        continue

                    # 命令
                    if "cmd" in data:
                        cmd_id = data.get("id", 0)
                        cmd = data["cmd"]
                        params = data.get("params", {})

                        print(f"📱 执行命令: {cmd} (id={cmd_id})")

                        # 执行命令
                        result = execute_command(cmd, params)
                        result["id"] = cmd_id

                        # 发送响应
                        await ws.send(json.dumps(result))
                        print(f"✅ 命令完成: {cmd}")

                except asyncio.TimeoutError:
                    # 超时，发送心跳
                    if time.time() - last_ping > HEARTBEAT_INTERVAL:
                        await ws.send(json.dumps({"type": "ping"}))
                        last_ping = time.time()
                except websockets.exceptions.ConnectionClosed:
                    print("⚠️ 连接已断开")
                    break

    except Exception as e:
        print(f"❌ 连接异常: {e}")
        return False

    return True


async def main():
    """主循环，自动重连。"""
    import argparse

    parser = argparse.ArgumentParser(description="Termux 手机控制代理")
    parser.add_argument("--server", default=SERVER_URL, help="服务器 WebSocket 地址")
    parser.add_argument("--key", default=API_KEY, help="认证密钥")
    parser.add_argument("--device", default=DEVICE_ID, help="设备标识")
    args = parser.parse_args()

    print("=" * 50)
    print("📱 Termux 手机控制代理")
    print("=" * 50)
    print(f"服务器: {args.server}")
    print(f"设备ID: {args.device}")
    print()

    # 检查 ADB
    ok, out = run_adb(["version"])
    if ok:
        print(f"✅ ADB 已安装: {out.split(chr(10))[0]}")
    else:
        print(f"❌ ADB 未安装: {out}")
        print("请运行: pkg install android-tools")
        return

    # 检查设备连接
    if not setup_local_adb():
        print("❌ ADB 未连接，无法控制手机")
        print()
        print("快速解决:")
        print("1. 设置 → 开发者选项 → 开启「无线调试」")
        print("2. 在无线调试页面查看配对码和端口")
        print("3. Termux 中运行:")
        print("   adb pair localhost:<端口> <配对码>")
        print("   adb connect localhost:<端口>")
        print()
        input("配置好后按 Enter 继续...")
        # 重新检查
        if not setup_local_adb():
            print("❌ 仍然无法连接，请检查配置")
            return

    # 连接循环
    while True:
        success = await connect_to_server(args.server, args.key, args.device)
        if not success:
            print(f"⏳ {RECONNECT_DELAY} 秒后重连...")
            await asyncio.sleep(RECONNECT_DELAY)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 已退出")
