"""ADB 直连手机控制模块 — 无需 ScreenMCP App。

功能：
  - 截图（返回 base64）
  - 点击/长按
  - 滑动
  - 输入文字
  - 按键（返回/Home/最近任务）
  - 获取屏幕文本（通过 OCR 或 UI dump）

使用前提：
  - 手机通过 USB 连接到服务器
  - ADB 已安装且设备已授权
"""
import base64
import os
import re
import subprocess
import tempfile
from typing import Optional
from typing import Tuple

from nonebot import logger

# ============================================================
# ADB 命令执行
# ============================================================

def _run_adb(args: list, timeout: int = 10) -> Tuple[bool, str]:
    """执行 ADB 命令，返回 (成功, 输出)。"""
    cmd = ["adb"] + args
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            text=True,
        )
        return result.returncode == 0, result.stdout.strip()
    except subprocess.TimeoutExpired:
        return False, "ADB 命令超时"
    except FileNotFoundError:
        return False, "ADB 未安装"
    except Exception as e:
        return False, str(e)


def _run_adb_shell(cmd: str, timeout: int = 10) -> Tuple[bool, str]:
    """执行 ADB shell 命令。"""
    return _run_adb(["shell", cmd], timeout)


# ============================================================
# 核心功能
# ============================================================

def check_device() -> bool:
    """检查是否有 ADB 设备连接。"""
    ok, output = _run_adb(["devices"])
    if not ok:
        return False
    lines = output.strip().split("\n")
    # 跳过第一行 "List of devices attached"
    for line in lines[1:]:
        if "\tdevice" in line:
            return True
    return False


def screenshot() -> Optional[str]:
    """截图并返回 base64 编码的 PNG 图片。"""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # 方法1：exec-out（更快，直接输出到 stdout）
        ok, output = _run_adb(["exec-out", "screencap", "-p"], timeout=15)
        if ok and output:
            # exec-out 输出的是二进制数据，需要编码
            # 但 subprocess 在 text=True 时会损坏二进制数据
            # 所以用另一种方式
            pass

        # 方法2：保存到设备再拉取
        device_path = "/sdcard/screenshot_tmp.png"
        _run_adb_shell(f"screencap -p {device_path}", timeout=15)
        ok, _ = _run_adb(["pull", device_path, tmp_path], timeout=15)
        _run_adb_shell(f"rm {device_path}")

        if ok and os.path.exists(tmp_path):
            with open(tmp_path, "rb") as f:
                img_data = f.read()
            return base64.b64encode(img_data).decode("utf-8")
        return None
    except Exception as e:
        logger.error(f"[ADB] 截图失败: {e}")
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def tap(x: int, y: int) -> bool:
    """点击屏幕坐标。"""
    ok, _ = _run_adb_shell(f"input tap {x} {y}")
    return ok


def long_press(x: int, y: int, duration_ms: int = 1000) -> bool:
    """长按屏幕坐标。"""
    ok, _ = _run_adb_shell(f"input swipe {x} {y} {x} {y} {duration_ms}")
    return ok


def swipe(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> bool:
    """滑动操作。"""
    ok, _ = _run_adb_shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}")
    return ok


def scroll_up() -> bool:
    """向上滑动（浏览内容）。"""
    return swipe(540, 800, 540, 1300, 300)


def scroll_down() -> bool:
    """向下滑动（浏览内容）。"""
    return swipe(540, 1300, 540, 800, 300)


def input_text(text: str) -> bool:
    """输入文字（需要先聚焦输入框）。"""
    # ADB input text 不支持中文，需要用 ADBKeyboard 或其他方式
    # 先尝试用 am broadcast 方式
    escaped = text.replace("'", "\\'").replace('"', '\\"')
    ok, _ = _run_adb_shell(f"input text '{escaped}'")
    return ok


def press_back() -> bool:
    """按返回键。"""
    ok, _ = _run_adb_shell("input keyevent KEYCODE_BACK")
    return ok


def press_home() -> bool:
    """按 Home 键。"""
    ok, _ = _run_adb_shell("input keyevent KEYCODE_HOME")
    return ok


def press_recents() -> bool:
    """按最近任务键。"""
    ok, _ = _run_adb_shell("input keyevent KEYCODE_APP_SWITCH")
    return ok


def get_current_activity() -> Optional[str]:
    """获取当前前台 Activity。"""
    ok, output = _run_adb_shell("dumpsys activity activities | grep mResumedActivity")
    if ok and output:
        return output.strip()
    return None


def open_app(package_name: str) -> bool:
    """打开应用（通过 monkey 启动主 Activity）。"""
    ok, _ = _run_adb_shell(f"monkey -p {package_name} -c android.intent.category.LAUNCHER 1")
    return ok


def get_screen_text() -> str:
    """获取屏幕文本（通过 UI dump）。"""
    device_path = "/sdcard/ui_dump.xml"
    _run_adb_shell(f"uiautomator dump {device_path}", timeout=15)
    ok, output = _run_adb_shell(f"cat {device_path}")
    _run_adb_shell(f"rm {device_path}")

    if ok and output:
        # 简单提取文本内容
        import re
        texts = re.findall(r'text="([^"]+)"', output)
        return "\n".join(texts[:50])  # 限制返回数量
    return "无法获取屏幕文本"


# ============================================================
# 指令解析（兼容 phone_control.py 的格式）
# ============================================================

# 常用应用包名
APP_MAP = {
    "微信": "com.tencent.mm",
    "QQ": "com.tencent.mobileqq",
    "抖音": "com.ss.android.ugc.aweme",
    "抖音极速版": "com.ss.android.ugc.aweme.lite",
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
}


def execute_adb_command(raw_msg: str) -> Optional[str]:
    """执行 ADB 命令，返回结果文本。"""
    if not check_device():
        return "📱 没有检测到 ADB 设备，请确认手机已通过 USB 连接并授权"

    msg = raw_msg.strip()

    # 截图
    if any(kw in msg for kw in ["截屏", "截图", "截个屏", "截个图"]):
        img_b64 = screenshot()
        if img_b64:
            return f"[CQ:image,file=base64://{img_b64}]"
        return "📱 截图失败"

    # 返回
    if any(kw in msg for kw in ["返回", "回退", "后退"]):
        if press_back():
            return "📱 已按返回键"
        return "📱 返回失败"

    # 回到桌面
    if any(kw in msg for kw in ["回到桌面", "回到主页", "返回桌面"]):
        if press_home():
            return "📱 已回到桌面"
        return "📱 回桌面失败"

    # 最近任务
    if "最近任务" in msg:
        if press_recents():
            return "📱 已打开最近任务"
        return "📱 打开最近任务失败"

    # 上滑
    if "上滑" in msg:
        # 检查是否有次数
        m = re.search(r"上滑\s*(\d+)\s*[下次]", msg)
        count = int(m.group(1)) if m else 1
        count = min(count, 10)
        for i in range(count):
            scroll_up()
        return f"📱 上滑{count}次完成"

    # 下滑
    if "下滑" in msg:
        m = re.search(r"下滑\s*(\d+)\s*[下次]", msg)
        count = int(m.group(1)) if m else 1
        count = min(count, 10)
        for i in range(count):
            scroll_down()
        return f"📱 下滑{count}次完成"

    # 点击坐标
    m = re.search(r"点击\s*(\d+)\s*[,，]\s*(\d+)", msg)
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        if tap(x, y):
            return f"📱 已点击 ({x}, {y})"
        return "📱 点击失败"

    # 输入文字
    m = re.search(r"(?:输入|打字|输入文字)\s*[：:]?\s*(.+)", msg)
    if m:
        text = m.group(1).strip()
        if input_text(text):
            return f"📱 已输入: {text[:30]}"
        return "📱 输入失败（ADB 不支持中文输入）"

    # 打开应用
    m = re.search(r"打开\s*(.+?)(?:\s*$|\s*[吧呢啊])", msg)
    if m:
        app_name = m.group(1).strip()
        package = APP_MAP.get(app_name)
        if package:
            if open_app(package):
                return f"📱 已打开 {app_name}"
            return f"📱 打开 {app_name} 失败"
        return f"📱 未知应用: {app_name}（目前支持: {', '.join(APP_MAP.keys())}）"

    # 当前界面
    if any(kw in msg for kw in ["当前界面", "屏幕状态", "当前应用"]):
        activity = get_current_activity()
        if activity:
            return f"📱 当前: {activity}"
        return "📱 无法获取当前界面"

    return None
