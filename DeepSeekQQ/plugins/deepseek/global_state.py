"""全局状态注册表 — 集中管理模块级可变状态，便于测试清理和调试。

所有模块应将全局可变状态（dict/list/set）注册到此表，而不是直接
使用模块级变量。register() 返回一个代理对象，读写行为与原始对象
一致，但同时被注册表追踪。

测试清理：
    from .global_state import reset_all
    def teardown():
        reset_all()

真人化 P1-3：全局状态迁移 — 所有模块级可变状态统一注册至此。
"""

from typing import Any
from typing import Dict
from typing import Optional

# ═══════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════

_registry: dict[str, tuple[Any, Any]] = {}
"""name → (current_value, initial_value)"""

# 命名空间隔离：{namespace: {name: (current, initial)}}
_namespaces: Dict[str, dict] = {}


def register(name: str, initial_value: Any, /, namespace: str = "default") -> Any:
    """注册全局可变状态并返回初始值。

    返回的正是 initial_value 本身（非代理），调用方像使用普通变量
    即可。注册表的目的是让 reset_all() 知道该清什么、清成什么。

    示例:
        _session_states = register("follow_up._session_states", {})
        _feed_store = register("social_feed._feed_store", [])

    Args:
        name: 状态变量名（在 namespace 内唯一）
        initial_value: 初始值
        namespace: 命名空间（默认 "default"），用于隔离不同模块
    """
    import copy
    # 保存初始值的深拷贝，防止 reset 时引用污染
    saved_initial = copy.deepcopy(initial_value)

    if namespace not in _namespaces:
        _namespaces[namespace] = {}

    # 同时注册到全局注册表和命名空间
    full_name = f"{namespace}:{name}" if namespace != "default" else name
    _registry[full_name] = (initial_value, saved_initial)
    _namespaces[namespace][name] = (initial_value, saved_initial)
    return initial_value


def reset_all(namespace: Optional[str] = None) -> None:
    """重置所有（或指定命名空间的）注册的全局状态到初始值。

    对于可变容器类型（dict/list/set），清空并填充初始值；
    对于不可变类型，直接替换引用。

    Args:
        namespace: 如果指定，只重置该命名空间；否则重置全部
    """
    targets = _registry
    if namespace is not None and namespace in _namespaces:
        targets = {
            f"{namespace}:{name}": val
            for name, val in _namespaces[namespace].items()
        }

    for name, (current, initial) in targets.items():
        _reset_to(current, initial)


def _reset_to(current: Any, initial: Any) -> None:
    """将 current 对象恢复到 initial 状态。

    initial 是 register() 时保存的深拷贝，不会被后续修改污染。
    """
    if current is initial:
        # 相同引用说明未被重新赋值，从 initial 恢复
        # 注意：initial 是深拷贝，不受 current 修改影响
        pass  # 下面统一处理

    if isinstance(current, dict) and isinstance(initial, dict):
        current.clear()
        import copy
        current.update(copy.deepcopy(initial))
    elif isinstance(current, list) and isinstance(initial, list):
        current.clear()
        import copy
        current.extend(copy.deepcopy(initial))
    elif isinstance(current, set) and isinstance(initial, set):
        current.clear()
        import copy
        current.update(copy.deepcopy(initial))
    # 不可变类型：无法通过引用恢复，由 register_snapshot 处理重新赋值的情况


def reset_single(name: str, namespace: str = "default") -> None:
    """重置单个注册项到初始值。

    Args:
        name: 状态变量名
        namespace: 命名空间
    """
    full_name = f"{namespace}:{name}" if namespace != "default" else name
    if full_name in _registry:
        current, initial = _registry[full_name]
        _reset_to(current, initial)


def register_snapshot(name: str, value: Any, initial: Any, namespace: str = "default") -> None:
    """当变量被重新赋值后调用此方法更新注册表。

    示例:
        _session_states = {}
        register_snapshot("follow_up._session_states", _session_states, {})
    """
    full_name = f"{namespace}:{name}" if namespace != "default" else name
    _registry[full_name] = (value, initial)
    if namespace not in _namespaces:
        _namespaces[namespace] = {}
    _namespaces[namespace][name] = (value, initial)


def get_registered(namespace: Optional[str] = None) -> dict[str, Any]:
    """返回所有已注册状态的快照（用于调试/健康检查）。

    Args:
        namespace: 如果指定，只返回该命名空间的状态
    """
    if namespace is not None and namespace in _namespaces:
        return {name: current for name, (current, _) in _namespaces[namespace].items()}
    return {name: current for name, (current, _) in _registry.items()}


def snapshot_all() -> Dict[str, Any]:
    """创建所有注册状态的深拷贝快照（用于测试前的状态保存）。

    Returns:
        {name: copied_value} — 可以用 restore_all() 恢复
    """
    import copy
    return {name: copy.deepcopy(current) for name, (current, _) in _registry.items()}


def restore_all(snapshot: Dict[str, Any]) -> None:
    """从 snapshot_all() 的快照恢复所有状态。

    Args:
        snapshot: snapshot_all() 的返回值
    """
    for name, value in snapshot.items():
        if name in _registry:
            current, _ = _registry[name]
            _restore_value(current, value)


def _restore_value(current: Any, target: Any) -> None:
    """将 current 恢复到 target 状态（深拷贝恢复）。"""
    import copy
    if isinstance(current, dict) and isinstance(target, dict):
        current.clear()
        current.update(copy.deepcopy(target))
    elif isinstance(current, list) and isinstance(target, list):
        current.clear()
        current.extend(copy.deepcopy(target))
    elif isinstance(current, set) and isinstance(target, set):
        current.clear()
        current.update(copy.deepcopy(target))
    # 对于不可变类型，通过 _registry 直接替换引用
