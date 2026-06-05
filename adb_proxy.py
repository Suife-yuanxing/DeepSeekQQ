"""ADB 代理服务 — 在本地电脑运行，供服务器远程调用。

功能：
  - 接收 HTTP 请求执行 ADB 命令
  - 返回截图（base64）、执行结果

使用：
  python adb_proxy.py

服务器通过 http://<本地IP>:9000/ 调用
"""
import re
import os
import subprocess
import base64
import tempfile
import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

PORT = 9000


def run_adb(args: list, timeout: int = 10) -> tuple:
    """执行 ADB 命令（列表参数，避免 shell 注入）。"""
    cmd = ["adb"] + args
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout
    except Exception as e:
        return False, str(e)


def _sanitize_adb_shell(shell_args: list, timeout: int = 10) -> tuple:
    """安全执行 adb shell 命令（列表参数，不经过 shell 解析）。"""
    return run_adb(["shell"] + shell_args, timeout=timeout)


def screenshot_base64() -> str:
    """截图并返回 base64。"""
    # 使用唯一临时文件名避免并发竞争
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(tmp_fd)
    device_path = f"/sdcard/screenshot_{os.getpid()}.png"

    try:
        _sanitize_adb_shell(["screencap", "-p", device_path], timeout=15)
        ok, _ = run_adb(["pull", device_path, tmp_path], timeout=15)
        _sanitize_adb_shell(["rm", device_path])

        if ok and os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f:
                return base64.b64encode(f.read()).decode("utf-8")
        return ""
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# 允许的 keyevent 名称白名单
_VALID_KEYEVENT = re.compile(r'^[A-Z_0-9]+$')


class ADBHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == "/screenshot":
            img_b64 = screenshot_base64()
            self.send_json({"success": bool(img_b64), "image": img_b64})

        elif path == "/tap":
            x_list, y_list = params.get("x"), params.get("y")
            if not x_list or not y_list:
                self.send_json({"success": False, "error": "缺少 x 或 y 参数"}, 400)
                return
            x, y = x_list[0], y_list[0]
            if not (x.isdigit() and y.isdigit()):
                self.send_json({"success": False, "error": "坐标必须是正整数"}, 400)
                return
            ok, _ = _sanitize_adb_shell(["input", "tap", x, y])
            self.send_json({"success": ok})

        elif path == "/swipe":
            x1 = params.get("x1", ["540"])[0]
            y1 = params.get("y1", ["800"])[0]
            x2 = params.get("x2", ["540"])[0]
            y2 = params.get("y2", ["1300"])[0]
            coords = [x1, y1, x2, y2]
            if not all(c.isdigit() for c in coords):
                self.send_json({"success": False, "error": "坐标必须是正整数"}, 400)
                return
            ok, _ = _sanitize_adb_shell(["input", "swipe", x1, y1, x2, y2, "300"])
            self.send_json({"success": ok})

        elif path == "/key":
            key = params.get("code", ["KEYCODE_HOME"])[0]
            if not _VALID_KEYEVENT.match(key):
                self.send_json({"success": False, "error": "非法按键码"}, 400)
                return
            ok, _ = _sanitize_adb_shell(["input", "keyevent", key])
            self.send_json({"success": ok})

        elif path == "/text":
            text = params.get("t", [""])[0]
            # ADB input text 使用 %s 代替空格，过滤其他危险字符
            safe_text = re.sub(r"[^a-zA-Z0-9一-龥.,;:!?@#%_+=\-/ ]", "", text)
            if not safe_text:
                self.send_json({"success": False, "error": "无有效文字"}, 400)
                return
            # 空格用 %s 代替（ADB shell input text 语法）
            adb_text = safe_text.replace(" ", "%s")
            ok, _ = _sanitize_adb_shell(["input", "text", adb_text])
            self.send_json({"success": ok})

        elif path == "/status":
            ok, output = run_adb(["devices"])
            devices = [
                line.split("\t")[0]
                for line in output.strip().split("\n")[1:]
                if "\tdevice" in line
            ]
            self.send_json({"success": True, "devices": devices})

        else:
            self.send_json({
                "error": "unknown endpoint",
                "endpoints": ["/screenshot", "/tap", "/swipe", "/key", "/text", "/status"],
            }, 404)

    def send_json(self, data: dict, status: int = 200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[ADB Proxy] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", PORT), ADBHandler)
    print(f"[ADB Proxy] 服务启动，端口 {PORT}（仅监听 127.0.0.1）")
    print(f"[ADB Proxy] 测试: http://localhost:{PORT}/status")
    server.serve_forever()
