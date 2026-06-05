"""ADB 代理服务 — 在本地电脑运行，供服务器远程调用。

功能：
  - 接收 HTTP 请求执行 ADB 命令
  - 返回截图（base64）、执行结果

使用：
  python adb_proxy.py

服务器通过 http://<本地IP>:9000/ 调用
"""
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
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return result.returncode == 0, result.stdout
    except Exception as e:
        return False, str(e).encode()


def _sanitize_adb_shell(shell_args: list, timeout: int = 10) -> tuple:
    """安全执行 adb shell 命令（列表参数，不经过 shell 解析）。"""
    return run_adb(["shell"] + shell_args, timeout=timeout)


def screenshot_base64() -> str:
    """截图并返回 base64。"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        device_path = "/sdcard/screenshot_tmp.png"
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


class ADBHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/screenshot":
            img_b64 = screenshot_base64()
            self.send_json({"success": bool(img_b64), "image": img_b64})

        elif path == "/tap":
            params = parse_qs(parsed.query)
            x, y = params.get("x", ["0"])[0], params.get("y", ["0"])[0]
            # 参数校验：必须是数字
            if not (x.isdigit() and y.isdigit()):
                self.send_json({"success": False, "error": "坐标必须是数字"})
                return
            ok, _ = _sanitize_adb_shell(["input", "tap", x, y])
            self.send_json({"success": ok})

        elif path == "/swipe":
            params = parse_qs(parsed.query)
            x1 = params.get("x1", ["540"])[0]
            y1 = params.get("y1", ["800"])[0]
            x2 = params.get("x2", ["540"])[0]
            y2 = params.get("y2", ["1300"])[0]
            coords = [x1, y1, x2, y2]
            if not all(c.isdigit() for c in coords):
                self.send_json({"success": False, "error": "坐标必须是数字"})
                return
            ok, _ = _sanitize_adb_shell(["input", "swipe", x1, y1, x2, y2, "300"])
            self.send_json({"success": ok})

        elif path == "/key":
            params = parse_qs(parsed.query)
            key = params.get("code", ["KEYCODE_HOME"])[0]
            # 白名单校验：只允许合法 keyevent 名称
            import re as _re
            if not _re.match(r'^[A-Z_0-9]+$', key):
                self.send_json({"success": False, "error": "非法按键码"})
                return
            ok, _ = _sanitize_adb_shell(["input", "keyevent", key])
            self.send_json({"success": ok})

        elif path == "/text":
            params = parse_qs(parsed.query)
            text = params.get("t", [""])[0]
            # 转义 ADB shell 特殊字符，防止注入
            # adb shell input text 支持的字符有限，空格用 %s 代替
            import re as _re
            safe_text = _re.sub(r"[^a-zA-Z0-9一-龥.,;:!?@#%_+=\-/]", "", text)
            if not safe_text:
                self.send_json({"success": False, "error": "无有效文字"})
                return
            ok, _ = _sanitize_adb_shell(["input", "text", safe_text])
            self.send_json({"success": ok})

        elif path == "/status":
            ok, output = run_adb(["devices"])
            devices = [l.split("\t")[0] for l in output.decode().strip().split("\n")[1:] if "\tdevice" in l]
            self.send_json({"success": True, "devices": devices})

        else:
            self.send_json({"error": "unknown endpoint", "endpoints": ["/screenshot", "/tap", "/swipe", "/key", "/text", "/status"]})

    def send_json(self, data: dict):
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        print(f"[ADB Proxy] {args[0]}")


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), ADBHandler)
    print(f"[ADB Proxy] 服务启动，端口 {PORT}")
    print(f"[ADB Proxy] 测试: http://localhost:{PORT}/status")
    server.serve_forever()
