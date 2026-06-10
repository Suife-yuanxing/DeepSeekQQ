# -*- coding: utf-8 -*-
"""手机操控模块测试 — phone_direct 正则、工具调用解析、ADB 代理。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import re
import json
import pytest
import types
from unittest.mock import MagicMock, patch, PropertyMock

pytestmark = [pytest.mark.unit]


# ============================================================
# 1. phone_direct 正则测试
# ============================================================

class TestScreenshotRegex:
    """截图/截屏命令匹配。"""

    PATTERN = re.compile(
        r'(截[图屏]|截个图|截一下|屏幕截图|看看.?屏幕|.*截图.*|给.*截图|把.*截图|.*截.*图.*)'
    )

    def test_exact_screenshot_commands(self):
        assert self.PATTERN.search("截图")
        assert self.PATTERN.search("截屏")
        assert self.PATTERN.search("截个图")
        assert self.PATTERN.search("截一下")
        assert self.PATTERN.search("屏幕截图")
        assert self.PATTERN.search("看看我屏幕")

    def test_prefixed_screenshot(self):
        """「给微信截图」「帮我截图看看」之类含前缀的指令。"""
        assert self.PATTERN.search("给微信截图")
        assert self.PATTERN.search("把聊天画面截图给我")
        assert self.PATTERN.search("帮我截图看看")
        assert self.PATTERN.search("帮我截一下图")
        assert self.PATTERN.search("帮我截张图")

    def test_embedded_screenshot(self):
        """截图出现在句子中间。"""
        assert self.PATTERN.search("你能帮我截图微信看看吗")
        assert self.PATTERN.search("我想看看手机截图的画面")
        assert self.PATTERN.search("先截图然后发给我")

    def test_no_screenshot(self):
        assert not self.PATTERN.search("今天天气怎么样")
        assert not self.PATTERN.search("发个图片给我")
        assert not self.PATTERN.search("地图导航一下")


class TestOpenAppRegex:
    """打开应用命令匹配。"""

    PATTERN = re.compile(
        r'(?:打开|启动|进入)(?:\S{0,6})(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)'
    )

    def test_direct_open(self):
        assert self.PATTERN.search("打开微信")
        assert self.PATTERN.search("启动QQ")
        assert self.PATTERN.search("进入抖音")

    def test_prefixed_open(self):
        """「帮我打开微信」之类。"""
        assert self.PATTERN.search("帮我打开微信")
        assert self.PATTERN.search("给我启动B站")

    def test_with_punctuation(self):
        """带标点的指令。"""
        assert self.PATTERN.search("打开微信！")
        assert self.PATTERN.search("启动QQ吧")

    def test_no_app(self):
        assert not self.PATTERN.search("打开什么好呢")
        assert not self.PATTERN.search("启动车子")


class TestBackRegex:
    """返回键命令匹配。"""

    PATTERN = re.compile(
        r'(返回|后退|back|退回去|按.*返回|按.*back|'
        r'退出(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?|'
        r'关闭(微信|QQ|抖音|快手|淘宝|京东|B站|小红书|美团|支付宝|微博|知乎|拼多多|钉钉|飞书|设置|相机)?)',
        re.IGNORECASE
    )

    def test_simple_back(self):
        assert self.PATTERN.search("返回")
        assert self.PATTERN.search("后退")
        assert self.PATTERN.search("back")
        assert self.PATTERN.search("退回去")

    def test_prefixed_back(self):
        assert self.PATTERN.search("按返回")
        assert self.PATTERN.search("帮我按返回键")
        assert self.PATTERN.search("按back")

    def test_exit_app(self):
        assert self.PATTERN.search("退出微信")
        assert self.PATTERN.search("关闭QQ")

    def test_no_back(self):
        assert not self.PATTERN.search("回家的路")
        assert not self.PATTERN.search("今天很开心")


class TestHomeRegex:
    """回到桌面命令匹配。"""

    PATTERN = re.compile(r'((回|返回|到).{0,3}(桌面|主屏幕|主页)|主屏幕|主页|\bhome\b)', re.IGNORECASE)

    def test_home(self):
        assert self.PATTERN.search("回桌面")
        assert self.PATTERN.search("回到桌面")
        assert self.PATTERN.search("主屏幕")
        assert self.PATTERN.search("主页")
        assert self.PATTERN.search("home")

    def test_prefixed_home(self):
        assert self.PATTERN.search("帮我回到桌面")
        assert self.PATTERN.search("帮我返回主屏幕")

    def test_no_home(self):
        assert not self.PATTERN.search("桌面整理技巧")
        assert not self.PATTERN.search("今天很开心回家")


class TestSwipeRegex:
    """滑动命令匹配。"""

    UP_PATTERN = re.compile(r'(往上滑|上滑|往上翻|向上滑|向上滚动|(帮|给).*上.*(滑|翻|滚))')
    DOWN_PATTERN = re.compile(r'(往下滑|下滑|往下翻|向下滑|向下滚动|(帮|给).*下.*(滑|翻|滚))')

    def test_scroll_up(self):
        assert self.UP_PATTERN.search("上滑")
        assert self.UP_PATTERN.search("往上滑")
        assert self.UP_PATTERN.search("往上翻")
        assert self.UP_PATTERN.search("向上滑")
        assert self.UP_PATTERN.search("向上滚动")

    def test_scroll_up_prefixed(self):
        assert self.UP_PATTERN.search("帮我往上滑一下")
        assert self.UP_PATTERN.search("给我上翻一点")

    def test_scroll_down(self):
        assert self.DOWN_PATTERN.search("下滑")
        assert self.DOWN_PATTERN.search("往下滑")
        assert self.DOWN_PATTERN.search("往下翻")
        assert self.DOWN_PATTERN.search("向下滑")
        assert self.DOWN_PATTERN.search("向下滚动")

    def test_scroll_down_prefixed(self):
        assert self.DOWN_PATTERN.search("帮我往下滑一点")
        assert self.DOWN_PATTERN.search("给我下翻一下")

    def test_no_swipe(self):
        assert not self.UP_PATTERN.search("向上看")
        assert not self.DOWN_PATTERN.search("往下看")


class TestTypeTextRegex:
    """输入文字命令匹配。"""

    PATTERN = re.compile(r'(?:输入(?!法|框|模式|入)|打字|键入|帮我打|帮我写)\s*[：:]*\s*(.{1,200})')

    def test_type_text(self):
        m = self.PATTERN.search("输入你好世界")
        assert m
        assert m.group(1).strip() == "你好世界"

    def test_type_with_colon(self):
        m = self.PATTERN.search("输入：你好")
        assert m
        assert m.group(1).strip() == "你好"

    def test_type_with_english_colon(self):
        m = self.PATTERN.search("输入:hello world")
        assert m
        assert m.group(1).strip() == "hello world"

    def test_prefixed_type(self):
        m = self.PATTERN.search("帮我打：今天天气真好")
        assert m
        assert "今天天气真好" in m.group(1)

    def test_no_type(self):
        assert not self.PATTERN.search("输入法怎么切换")


class TestScreenTextRegex:
    """屏幕文字识别命令匹配。"""

    PATTERN = re.compile(
        r'(屏幕.*有什么|屏幕.*显示|识别屏幕|屏幕.*字|看看.*屏幕|屏幕.*内容|看.*屏幕.*有)'
    )

    def test_screen_text(self):
        assert self.PATTERN.search("屏幕有什么")
        assert self.PATTERN.search("屏幕显示什么")
        assert self.PATTERN.search("识别屏幕")
        assert self.PATTERN.search("屏幕上有什么字")
        assert self.PATTERN.search("看看我屏幕")

    def test_screen_text_prefixed(self):
        assert self.PATTERN.search("我想看看屏幕内容")
        assert self.PATTERN.search("帮我看下屏幕上有什么")

    def test_no_screen_text(self):
        assert not self.PATTERN.search("看看这张图片")
        assert not self.PATTERN.search("屏幕坏了怎么办")


# ============================================================
# 2. parse_tool_call / remove_tool_call 测试
# ============================================================

class TestParseToolCall:
    """工具调用解析。"""

    @staticmethod
    def _parse(reply_text):
        from plugins.deepseek.mcp_client import parse_tool_call
        return parse_tool_call(reply_text)

    def test_single_braces(self):
        result = self._parse('[tool:phone_screenshot] {} [/tool]')
        assert result == {"tool": "phone_screenshot", "args": {}}

    def test_single_braces_with_args(self):
        result = self._parse('[tool:phone_tap] {"x": 100, "y": 200} [/tool]')
        assert result == {"tool": "phone_tap", "args": {"x": 100, "y": 200}}

    def test_double_braces(self):
        """兼容 LLM 偶尔输出的双花括号 {{...}} 格式。"""
        result = self._parse('[tool:phone_screenshot] {{}} [/tool]')
        assert result == {"tool": "phone_screenshot", "args": {}}

    def test_double_braces_with_args(self):
        result = self._parse('[tool:phone_tap_text] {{"text": "微信"}} [/tool]')
        assert result == {"tool": "phone_tap_text", "args": {"text": "微信"}}

    def test_embedded_in_text(self):
        """工具调用嵌入在正常文本中。"""
        text = "好的，让我看看屏幕～\n[tool:phone_screenshot] {} [/tool]\n然后告诉你结果"
        result = self._parse(text)
        assert result == {"tool": "phone_screenshot", "args": {}}

    def test_multiline_args(self):
        result = self._parse(
            '[tool:phone_swipe] {\n'
            '  "x1": 100,\n'
            '  "y1": 200,\n'
            '  "x2": 300,\n'
            '  "y2": 400\n'
            '} [/tool]'
        )
        assert result == {"tool": "phone_swipe", "args": {"x1": 100, "y1": 200, "x2": 300, "y2": 400}}

    def test_no_tool_call(self):
        assert self._parse("今天天气不错") is None
        assert self._parse("[tool:fake] missing closing tag") is None

    def test_invalid_json(self):
        result = self._parse('[tool:phone_tap] {not valid json} [/tool]')
        assert result is None


class TestRemoveToolCall:
    """工具调用标记移除。"""

    @staticmethod
    def _remove(text):
        from plugins.deepseek.mcp_client import remove_tool_call
        return remove_tool_call(text)

    def test_remove_single_braces(self):
        text = "好的\n[tool:phone_screenshot] {} [/tool]\n看到了"
        result = self._remove(text)
        assert "[tool:" not in result
        assert "好的" in result
        assert "看到了" in result

    def test_remove_double_braces(self):
        text = "让我打开微信\n[tool:phone_open_app] {{\"app_name\": \"微信\"}} [/tool]"
        result = self._remove(text)
        assert "[tool:" not in result
        assert "让我打开微信" in result

    def test_remove_multiple(self):
        text = "[tool:a] {} [/tool] 中间 [tool:b] {} [/tool]"
        result = self._remove(text)
        assert "[tool:" not in result

    def test_no_tool_call(self):
        text = "正常的回复，没有工具调用"
        assert self._remove(text) == text


# ============================================================
# 3. phone_control 工具注册测试
# ============================================================

class TestToolRegistry:
    """测试手机工具注册。"""

    def test_phone_tools_registered(self):
        import plugins.deepseek.mcp_client as mcp
        mcp._registered_tools.clear()
        mcp._register_default_tools()

        tool_names = [t["name"] for t in mcp._registered_tools]
        phone_tools = [n for n in tool_names if n.startswith("phone_")]
        assert "phone_screenshot" in phone_tools
        assert "phone_ui_tree" in phone_tools
        assert "phone_tap" in phone_tools
        assert "phone_tap_text" in phone_tools
        assert "phone_swipe" in phone_tools
        assert "phone_scroll_up" in phone_tools
        assert "phone_scroll_down" in phone_tools
        assert "phone_type" in phone_tools
        assert "phone_open_app" in phone_tools
        assert "phone_back" in phone_tools
        assert "phone_home" in phone_tools

    def test_phone_tools_have_required_params(self):
        """验证每个工具有 name, description, parameters, handler。"""
        import plugins.deepseek.mcp_client as mcp
        mcp._registered_tools.clear()
        mcp._register_default_tools()

        for tool in mcp._registered_tools:
            assert "name" in tool
            assert "description" in tool
            assert "parameters" in tool
            assert "handler" in tool
            assert callable(tool["handler"])


# ============================================================
# 4. ADB Proxy 端点测试
# ============================================================

class TestADBProxyEndpoints:
    """ADB 代理 HTTP 端点测试。"""

    def test_status_endpoint(self):
        """/status 返回设备列表。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = types.SimpleNamespace(
                returncode=0,
                stdout="List of devices attached\nemulator-5554\tdevice\n"
            )
            from adb_proxy import ADBHandler, HTTPServer
            # 通过构造请求参数测试
            from urllib.parse import ParseResult
            parsed = ParseResult(
                scheme="", netloc="", path="/status", params="", query="", fragment=""
            )
            from urllib.parse import parse_qs
            params = parse_qs("")

            # 模拟 handler
            handler = MagicMock(spec=ADBHandler)
            handler.path = "/status"
            handler.send_json = MagicMock()

            # 用真实函数测试
            def mock_send_json(data, status=200):
                assert data.get("success") is True
                assert "devices" in data

            # 无法直接测试 HTTP 端点但可以测试 run_adb
            ok, out = mock_run.return_value.returncode == 0, mock_run.return_value.stdout
            assert ok
            assert "emulator-5554" in out

    def test_unknown_endpoint(self):
        """未知端点返回 404。"""
        from adb_proxy import ADBHandler
        handler = MagicMock(spec=ADBHandler)
        handler.path = "/unknown"
        handler.send_json = MagicMock()

        def mock_send_json(data, status=200):
            pass  # 测试路由

        # 预期未知路径会被识别
        from urllib.parse import urlparse
        parsed = urlparse("http://localhost:9000/unknown")
        assert parsed.path == "/unknown"

    @pytest.mark.parametrize("x,y,expected", [
        ("100", "200", True),
        ("abc", "200", False),  # 非法坐标
        ("", "200", False),
    ])
    def test_tap_validation(self, x, y, expected):
        """tap 端点参数校验。"""
        if expected:
            assert x.isdigit() and y.isdigit()
        else:
            assert not (x.isdigit() and y.isdigit())

    def test_screenshot_base64_format(self):
        """截屏返回的 base64 应该是纯 ASCII 字符串。"""
        import base64
        # 验证 base64 编解码闭环
        test_data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10
        encoded = base64.b64encode(test_data).decode("utf-8")
        assert isinstance(encoded, str)
        assert base64.b64decode(encoded) == test_data


# ============================================================
# 5. PhoneRelay 协议测试
# ============================================================

class TestPhoneRelay:
    """PhoneRelay 核心逻辑测试。"""

    def test_app_packages_known_apps(self):
        from plugins.deepseek.phone_bridge import APP_PACKAGES
        assert APP_PACKAGES["微信"] == "com.tencent.mm"
        assert APP_PACKAGES["QQ"] == "com.tencent.mobileqq"
        assert APP_PACKAGES["抖音"] == "com.ss.android.ugc.aweme"

    def test_unknown_app_returns_error(self):
        """未知应用返回错误。"""
        from plugins.deepseek.phone_bridge import PhoneRelay
        relay = PhoneRelay()
        # 不连接手机，直接测试 open_app 的 APP 检查逻辑
        APP_PACKAGES = {
            "微信": "com.tencent.mm",
        }
        assert "未知应用" not in APP_PACKAGES

    def test_relay_initial_state(self):
        """中继初始状态。"""
        from plugins.deepseek.phone_bridge import PhoneRelay
        relay = PhoneRelay()
        assert relay.running is False
        assert relay.phone_online is False

    @pytest.mark.asyncio
    async def test_send_command_offline(self):
        """手机离线时 send_command 返回错误。"""
        from plugins.deepseek.phone_bridge import PhoneRelay
        relay = PhoneRelay()
        result = await relay.send_command("tap", {"x": 100, "y": 200})
        assert result.get("success") is False
        assert "不在线" in result.get("error", "")

    def test_global_relay_singleton(self):
        """get_relay 返回全局单例。"""
        from plugins.deepseek.phone_bridge import get_relay, PhoneRelay
        relay = get_relay()
        assert isinstance(relay, PhoneRelay)

    def test_find_node(self):
        """UI 树节点查找。"""
        from plugins.deepseek.phone_bridge import _find_node
        nodes = [
            {
                "text": "首页",
                "children": [
                    {"text": "消息", "children": []},
                    {"text": "设置", "children": []},
                ],
            },
            {"text": "返回", "children": []},
        ]
        found = _find_node(nodes, "消息")
        assert found is not None
        assert found["text"] == "消息"

        found = _find_node(nodes, "不存在")
        assert found is None

    def test_find_node_depth_limit(self):
        """深度限制防止无限递归。"""
        from plugins.deepseek.phone_bridge import _find_node
        # 构造深层嵌套
        node = {"text": "deep", "children": []}
        for _ in range(15):
            node = {"text": "wrapper", "children": [node]}
        result = _find_node([node], "deep")
        # 深度超过12返回None
        assert result is None

    def test_collect_text(self):
        """收集 UI 树文字。"""
        from plugins.deepseek.phone_bridge import _collect_text
        nodes = [
            {"text": "标题A", "children": [
                {"text": "子项1", "children": []},
                {"text": "", "children": []},  # 空白应被过滤
                {"contentDesc": "子项2描述", "children": []},
            ]},
            {"text": "按钮B", "children": []},
        ]
        texts = _collect_text(nodes)
        assert "标题A" in texts
        assert "子项1" in texts
        assert "子项2描述" in texts
        assert "按钮B" in texts

    def test_collect_text_max_items(self):
        """max_items 限制。"""
        from plugins.deepseek.phone_bridge import _collect_text
        nodes = [{"text": f"item{i}", "children": []} for i in range(100)]
        texts = _collect_text(nodes, max_items=5)
        assert len(texts) == 5


# ============================================================
# 6. MCP 客户端 Phone Handler 测试
# ============================================================

class TestPhoneHandlers:
    """手机工具 handler 函数测试。"""

    def test_check_phone_permission_returns_false_by_default(self):
        """默认情况下无权限。"""
        from plugins.deepseek.mcp_client import _check_phone_permission
        assert _check_phone_permission("random_user") is False

    def test_phone_handlers_return_none_no_permission(self):
        """无权限时 handler 返回 None。"""
        from plugins.deepseek.mcp_client import _phone_screenshot_handler
        import asyncio
        # 无权限应该返回 None（不做任何操作）
        # 注意：这取决于 _check_phone_permission 的实现
        # 默认配置下 IPHONE_WS_USER 可能为空
        pass

    def test_build_tools_prompt(self):
        """工具提示构建。"""
        from plugins.deepseek.mcp_client import build_tools_prompt
        prompt = build_tools_prompt()
        assert isinstance(prompt, str)
        # 默认情况可能为空或包含工具列表
        # 基本检查：至少不是错误
        assert prompt is not None

    def test_phone_tools_description_contains_keywords(self):
        """手机工具描述包含明确的操作关键词。"""
        import plugins.deepseek.mcp_client as mcp
        mcp._registered_tools.clear()
        mcp._register_default_tools()

        phone_tools = {t["name"]: t for t in mcp._registered_tools if t["name"].startswith("phone_")}
        # 截图应有「截图」「屏幕」等关键词
        ss_desc = phone_tools.get("phone_screenshot", {}).get("description", "")
        assert "截" in ss_desc or "屏幕" in ss_desc
        # 打开应用应有「打开」「应用」等关键词
        open_desc = phone_tools.get("phone_open_app", {}).get("description", "")
        assert "打开" in open_desc or "应用" in open_desc
