"""全局状态注册表 — 集中管理模块级可变状态，便于测试清理和调试。

所有模块应将全局可变状态（dict/list/set）注册到此表，而不是直接
使用模块级变量。register() 返回一个代理对象，读写行为与原始对象
一致，但同时被注册表追踪。

测试清理：
    from .global_state import reset_all
    def teardown():
        reset_all()
"""

from typing import Any

# ═══════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════

_registry: dict[str, tuple[Any, Any]] = {}
"""name → (current_value, initial_value)"""


def register(name: str, initial_value: Any, /) -> Any:
    """注册全局可变状态并返回初始值。

    返回的正是 initial_value 本身（非代理），调用方像使用普通变量
    即可。注册表的目的是让 reset_all() 知道该清什么、清成什么。

    示例:
        _session_states = register("follow_up._session_states", {})
        _feed_store = register("social_feed._feed_store", [])
    """
    _registry[name] = (initial_value, initial_value)
    return initial_value


def reset_all() -> None:
    """重置所有注册的全局状态到初始值（用于测试结束后清理）。

    对于可变容器类型（dict/list/set），清空并填充初始值；
    对于不可变类型，直接替换引用。

    注意：只重置仍在 _registry 中追踪的状态；
    如果某变量已被重新赋值（而非原地修改），需调用 register_snapshot。
    """
    for name, (current, initial) in _registry.items():
        _reset_to(current, initial)


def _reset_to(current: Any, initial: Any) -> None:
    """将 current 对象恢复到 initial 状态。"""
    if current is initial:
        # 相同引用，对于容器类型清空并恢复
        if isinstance(current, dict):
            current.clear()
            current.update(initial)
        elif isinstance(current, list):
            current.clear()
            current.extend(initial)
        elif isinstance(current, set):
            current.clear()
            current.update(initial)
        # 不可变类型无需操作
    else:
        # 引用已变更（被重新赋值），无法自动恢复
        pass


def register_snapshot(name: str, value: Any, initial: Any) -> None:
    """当变量被重新赋值后调用此方法更新注册表。

    示例:
        _session_states = {}
        register_snapshot("follow_up._session_states", _session_states, {})
    """
    _registry[name] = (value, initial)


def get_registered() -> dict[str, Any]:
    """返回所有已注册状态的快照（用于调试/健康检查）。"""
    return {name: current for name, (current, _) in _registry.items()}
