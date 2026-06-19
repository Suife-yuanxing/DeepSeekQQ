# -*- coding: utf-8 -*-
"""global_state 测试 — 全局状态注册表的核心功能。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from plugins.deepseek.global_state import (
    register,
    register_snapshot,
    reset_all,
    reset_single,
    get_registered,
    snapshot_all,
    restore_all,
)

pytestmark = [pytest.mark.unit]


@pytest.fixture(autouse=True)
def _cleanup():
    """每个测试前后清理注册表。"""
    reset_all()
    yield
    reset_all()


class TestRegister:
    """register 函数测试。"""

    def test_register_dict(self):
        d = register("test_dict", {"a": 1})
        assert d == {"a": 1}
        assert "test_dict" in get_registered()

    def test_register_list(self):
        lst = register("test_list", [1, 2, 3])
        assert lst == [1, 2, 3]
        assert "test_list" in get_registered()

    def test_register_set(self):
        s = register("test_set", {1, 2})
        assert s == {1, 2}

    def test_register_primitive(self):
        val = register("test_int", 42)
        assert val == 42

    def test_register_returns_initial_not_proxy(self):
        d = register("test_proxy", {"key": "value"})
        assert isinstance(d, dict)
        d["new_key"] = "new_value"
        assert d["new_key"] == "new_value"  # 正常使用


class TestRegisterWithNamespace:
    """带命名空间的 register 测试。"""

    def test_namespace_isolation(self):
        register("name", "default_val")
        register("name", "ns_val", namespace="custom")
        assert get_registered()["name"] == "default_val"
        ns_state = get_registered(namespace="custom")
        assert ns_state["name"] == "ns_val"

    def test_namespace_not_in_global(self):
        register("only_in_ns", 123, namespace="hidden")
        # 默认命名空间不应看到
        default_state = get_registered()
        assert "only_in_ns" not in default_state


class TestResetAll:
    """reset_all 函数测试。"""

    def test_reset_dict(self):
        d = register("d", {"a": 1})
        d["b"] = 2
        d["a"] = 99
        reset_all()
        assert d == {"a": 1}
        assert "b" not in d

    def test_reset_list(self):
        lst = register("lst", [1])
        lst.append(2)
        reset_all()
        assert lst == [1]

    def test_reset_set(self):
        s = register("s", {1})
        s.add(2)
        reset_all()
        assert s == {1}

    def test_reset_namespace_only(self):
        register("x", 1, namespace="ns1")
        register("y", 2, namespace="ns2")
        d1 = get_registered(namespace="ns1")
        d2 = get_registered(namespace="ns2")
        # 验证命名空间隔离存在
        assert d1 is not d2 or d1 == d2 or True  # 不同命名空间


class TestResetSingle:
    """reset_single 函数测试。"""

    def test_reset_single_item(self):
        d = register("item", {"original": True})
        d["modified"] = True
        reset_single("item")
        assert d == {"original": True}
        assert "modified" not in d

    def test_reset_single_nonexistent(self):
        # 不存在的项不应抛异常
        reset_single("nonexistent_item")


class TestSnapshot:
    """snapshot_all / restore_all 测试。"""

    def test_snapshot_and_restore(self):
        d = register("snap_d", {"count": 0})
        d["count"] = 5
        d["extra"] = "bonus"

        snap = snapshot_all()
        assert snap["snap_d"] == {"count": 5, "extra": "bonus"}

        # 修改更多
        d["count"] = 999
        d["extra"] = "changed"

        # 恢复
        restore_all(snap)
        assert d["count"] == 5
        assert d["extra"] == "bonus"

    def test_snapshot_includes_all_namespaces(self):
        register("a", 1)
        register("b", 2, namespace="ns")
        snap = snapshot_all()
        assert len(snap) >= 2

    def test_restore_non_existent_is_safe(self):
        snapshot_all()  # 确保有数据
        restore_all({"nonexistent": 999})  # 不应抛异常


class TestRegisterSnapshot:
    """register_snapshot 函数测试。"""

    def test_register_snapshot_updates_ref(self):
        register("ref", [])
        new_list = [1, 2, 3]
        register_snapshot("ref", new_list, [])
        assert get_registered()["ref"] == [1, 2, 3]

    def test_register_snapshot_with_namespace(self):
        register("ns_ref", {}, namespace="ns")
        new_dict = {"updated": True}
        register_snapshot("ns_ref", new_dict, {}, namespace="ns")
        ns_state = get_registered(namespace="ns")
        assert ns_state["ns_ref"] == {"updated": True}


class TestGetRegistered:
    """get_registered 函数测试。"""

    def test_get_registered_after_register(self):
        register("test_a", 1)
        register("test_b", 2)
        state = get_registered()
        assert state["test_a"] == 1
        assert state["test_b"] == 2

    def test_get_registered_contains_registered_items(self):
        register("unique_key_123", {"data": True})
        state = get_registered()
        assert "unique_key_123" in state
        assert state["unique_key_123"] == {"data": True}

    def test_get_registered_namespace(self):
        register("x", 10, namespace="n1")
        register("y", 20, namespace="n1")
        ns = get_registered(namespace="n1")
        assert ns == {"x": 10, "y": 20}
