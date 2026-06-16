"""Test MCP Client — MCP 工具注册/发现/权限检查。

C-6: 覆盖工具注册表、权限检查、工具 prompt 构建。
"""
import pytest
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# 工具注册表
# ═══════════════════════════════════════════════════════════════

class TestToolRegistry:
    """测试 MCP 工具注册表。"""

    def test_register_tool_adds_to_registry(self):
        """注册工具应添加到 _registered_tools。"""
        from plugins.deepseek.mcp_client import register_tool, _registered_tools
        initial_count = len(_registered_tools)
        register_tool("test_tool", "A test tool", {}, lambda: None)
        assert len(_registered_tools) == initial_count + 1

    def test_get_available_tools_returns_list(self):
        """get_available_tools 应返回列表。"""
        from plugins.deepseek.mcp_client import get_available_tools
        tools = get_available_tools()
        assert isinstance(tools, list)

    def test_tool_has_required_fields(self):
        """每个工具应有 name/description/parameters。"""
        from plugins.deepseek.mcp_client import get_available_tools
        tools = get_available_tools()
        for tool in tools:
            assert "name" in tool, f"Tool missing name"
            assert "description" in tool, f"Tool {tool.get('name', '?')} missing description"
            assert "parameters" in tool, f"Tool {tool.get('name', '?')} missing parameters"


# ═══════════════════════════════════════════════════════════════
# 权限检查
# ═══════════════════════════════════════════════════════════════

class TestPermissionCheck:
    """测试 MCP 工具权限检查。"""

    def test_set_phone_user(self):
        """设置手机用户 ID。"""
        from plugins.deepseek.mcp_client import set_phone_user
        set_phone_user("12345")
        # 不应抛异常


# ═══════════════════════════════════════════════════════════════
# 工具 Prompt 构建
# ═══════════════════════════════════════════════════════════════

class TestToolPrompt:
    """测试工具 prompt 构建。"""

    def test_build_tools_prompt_returns_string(self):
        """工具 prompt 应返回有效字符串。"""
        from plugins.deepseek.mcp_client import build_tools_prompt
        prompt = build_tools_prompt()
        assert isinstance(prompt, str)

    def test_build_tools_prompt_contains_tool_names(self):
        """工具 prompt 应包含已注册的工具名称。"""
        from plugins.deepseek.mcp_client import build_tools_prompt, get_available_tools, register_tool
        # 确保至少有一个工具
        register_tool("_test_search", "搜索测试工具", {"query": {"type": "string"}}, lambda: None)
        tools = get_available_tools()
        if tools:
            prompt = build_tools_prompt()
            found = any(t["name"] in prompt for t in tools)
            assert found, f"No tool names found in prompt"


# ═══════════════════════════════════════════════════════════════
# PhoneRelay 模拟
# ═══════════════════════════════════════════════════════════════

class TestPhoneRelay:
    """测试 PhoneRelay 基本功能。"""

    def test_relay_singleton(self):
        """get_relay 应返回单例。"""
        from plugins.deepseek.phone_bridge import get_relay
        relay1 = get_relay()
        relay2 = get_relay()
        assert relay1 is relay2

    def test_relay_initial_state(self):
        """初始状态应为离线。"""
        from plugins.deepseek.phone_bridge import get_relay
        relay = get_relay()
        assert relay.running is False
        assert relay.phone_online is False

    def test_relay_app_packages(self):
        """应用包名映射应包含常用 App。"""
        from plugins.deepseek.phone_bridge import APP_PACKAGES
        assert "微信" in APP_PACKAGES
        assert "QQ" in APP_PACKAGES
        assert APP_PACKAGES["微信"] == "com.tencent.mm"
