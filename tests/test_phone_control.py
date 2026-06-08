"""手机控制模块测试 — 覆盖指令解析 + 应用映射 + 安全限制。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import patch
import plugins.deepseek.phone_control as pc_mod
from plugins.deepseek.phone_control import (
    parse_phone_command, is_phone_command, APP_MAP, DIRECT_COMMANDS,
)

pytestmark = [pytest.mark.unit]


class TestParsePhoneCommand:
    """指令解析测试。"""

    def test_direct_back(self):
        result = parse_phone_command("返回")
        assert result is not None
        assert result["action"] == "back"

    def test_direct_home(self):
        result = parse_phone_command("回到桌面")
        assert result is not None
        assert result["action"] == "home"

    def test_scroll_up(self):
        result = parse_phone_command("上滑")
        assert result is not None
        assert result["action"] == "scroll"

    def test_scroll_down(self):
        result = parse_phone_command("往下滑")
        assert result is not None
        assert result["action"] == "scroll"

    def test_screenshot(self):
        result = parse_phone_command("截屏")
        assert result is not None
        assert result["action"] == "screenshot"

    def test_screenshot_variant(self):
        result = parse_phone_command("截个图")
        assert result is not None
        assert result["action"] == "screenshot"

    def test_open_wechat(self):
        result = parse_phone_command("打开微信")
        assert result is not None
        assert result["action"] == "open_app"
        assert result["params"]["app_name"] == "微信"

    def test_open_douyin(self):
        result = parse_phone_command("打开抖音")
        assert result is not None
        assert result["action"] == "open_app"
        assert result["params"]["app_name"] == "抖音"

    def test_open_settings(self):
        result = parse_phone_command("打开设置")
        assert result is not None
        assert result["action"] == "open_app"
        assert result["params"]["app_name"] == "设置"

    def test_click_by_text(self):
        result = parse_phone_command("点击确定")
        assert result is not None
        assert result["action"] == "click_element"
        assert result["params"]["text"] == "确定"

    def test_click_by_coords(self):
        result = parse_phone_command("点击500,300")
        assert result is not None
        assert result["action"] == "tap"
        assert result["params"]["x"] == 500
        assert result["params"]["y"] == 300

    def test_type_text(self):
        result = parse_phone_command("输入你好世界")
        assert result is not None
        assert result["action"] == "type_text"
        assert result["params"]["text"] == "你好世界"

    def test_scroll_multi_up(self):
        result = parse_phone_command("上滑3次")
        assert result is not None
        assert result["action"] == "scroll_multi"
        assert result["params"]["direction"] == "up"
        assert result["params"]["count"] == 3

    def test_scroll_multi_capped(self):
        result = parse_phone_command("往下滑20下")
        assert result is not None
        assert result["params"]["count"] == 10  # 上限 10

    def test_not_phone_command(self):
        result = parse_phone_command("今天天气怎么样")
        assert result is None

    def test_greeting_not_command(self):
        result = parse_phone_command("你好呀")
        assert result is None


class TestIsPhoneCommand:
    """指令识别测试。"""

    def test_positive_keywords(self):
        with patch.object(pc_mod, "PHONE_CONTROL_ENABLED", True):
            assert is_phone_command("打开微信") is True
            assert is_phone_command("截个屏") is True
            assert is_phone_command("返回桌面") is True
            assert is_phone_command("手机截屏") is True

    def test_negative(self):
        with patch.object(pc_mod, "PHONE_CONTROL_ENABLED", True):
            assert is_phone_command("今天吃什么") is False
            assert is_phone_command("你好") is False
            assert is_phone_command("") is False

    def test_disabled(self):
        with patch.object(pc_mod, "PHONE_CONTROL_ENABLED", False):
            assert is_phone_command("打开微信") is False


class TestAppMap:
    """应用包名映射测试。"""

    def test_common_apps(self):
        assert APP_MAP["微信"] == "com.tencent.mm"
        assert APP_MAP["QQ"] == "com.tencent.mobileqq"
        assert APP_MAP["抖音"] == "com.ss.android.ugc.aweme"
        assert APP_MAP["B站"] == "tv.danmaku.bili"
        assert APP_MAP["支付宝"] == "com.eg.android.AlipayGphone"

    def test_system_apps(self):
        assert APP_MAP["设置"] == "com.android.settings"
        assert APP_MAP["相机"] == "com.android.camera"


class TestDirectCommands:
    """直接指令映射测试。"""

    def test_navigation_commands(self):
        """导航指令应映射到正确的 ScreenMCP 工具。"""
        assert DIRECT_COMMANDS["返回"] == ("back", {})
        assert DIRECT_COMMANDS["回到桌面"] == ("home", {})
        assert DIRECT_COMMANDS["最近任务"] == ("recents", {})

    def test_no_dangerous_commands(self):
        """直接指令中不应包含危险操作。"""
        dangerous = {"factory_reset", "wipe_data", "reboot", "shutdown"}
        for keyword, (cmd, params) in DIRECT_COMMANDS.items():
            assert cmd not in dangerous, f"危险操作 {cmd} 不应在直接指令中"
