"""功能⑥：插件化热加载 — PluginManager 测试。"""
import pytest
import asyncio
from plugins.deepseek.plugin_manager import (
    BasePlugin, PluginMeta, register_plugin, get_enabled_plugins,
    list_plugins, disable_plugin, enable_plugin, get_plugin_by_name,
    _plugins, _loaded,
)


@pytest.fixture(autouse=True)
def clean_plugins():
    """每个测试前清理插件注册表。"""
    _plugins.clear()
    import plugins.deepseek.plugin_manager as pm
    pm._loaded = False
    yield
    _plugins.clear()


class TestPluginMeta:
    def test_default_values(self):
        meta = PluginMeta(name="test")
        assert meta.name == "test"
        assert meta.enabled is True
        assert meta.priority == 50
        assert meta.stage_name == ""

    def test_custom_values(self):
        meta = PluginMeta(name="custom", description="desc", enabled=False, priority=10, stage_name="llm_call")
        assert meta.name == "custom"
        assert meta.enabled is False
        assert meta.priority == 10
        assert meta.stage_name == "llm_call"


class TestBasePlugin:
    def test_subclass(self):
        class MyPlugin(BasePlugin):
            meta = PluginMeta(name="my")
        p = MyPlugin()
        assert p.meta.name == "my"

    @pytest.mark.asyncio
    async def test_on_message_returns_none(self):
        class MyPlugin(BasePlugin):
            meta = PluginMeta(name="my")
        p = MyPlugin()
        result = await p.on_message(None)
        assert result is None


class TestPluginRegistry:
    def test_register_plugin(self):
        class P(BasePlugin):
            meta = PluginMeta(name="test1")
        register_plugin(P())
        assert len(_plugins) == 1

    def test_register_duplicate_skipped(self):
        class P(BasePlugin):
            meta = PluginMeta(name="dup")
        register_plugin(P())
        register_plugin(P())
        assert len(_plugins) == 1

    def test_register_non_plugin_rejected(self):
        register_plugin("not a plugin")
        assert len(_plugins) == 0

    def test_get_enabled_sorted_by_priority(self):
        class P1(BasePlugin):
            meta = PluginMeta(name="p1", priority=30)
        class P2(BasePlugin):
            meta = PluginMeta(name="p2", priority=10)
        class P3(BasePlugin):
            meta = PluginMeta(name="p3", priority=20, enabled=False)
        register_plugin(P1())
        register_plugin(P2())
        register_plugin(P3())
        enabled = get_enabled_plugins()
        assert len(enabled) == 2
        assert enabled[0].meta.name == "p2"
        assert enabled[1].meta.name == "p1"

    def test_disable_enable_plugin(self):
        class P(BasePlugin):
            meta = PluginMeta(name="toggle")
        register_plugin(P())
        assert disable_plugin("toggle") is True
        assert _plugins[0].meta.enabled is False
        assert enable_plugin("toggle") is True
        assert _plugins[0].meta.enabled is True

    def test_disable_nonexistent(self):
        assert disable_plugin("no_such") is False

    def test_get_by_name(self):
        class P(BasePlugin):
            meta = PluginMeta(name="finder")
        register_plugin(P())
        assert get_plugin_by_name("finder") is not None
        assert get_plugin_by_name("nope") is None

    def test_list_plugins(self):
        class P(BasePlugin):
            meta = PluginMeta(name="listed", description="A plugin")
        register_plugin(P())
        result = list_plugins()
        assert len(result) == 1
        assert result[0]["name"] == "listed"
        assert result[0]["description"] == "A plugin"
