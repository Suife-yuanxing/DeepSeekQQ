"""
P0-3 Memory Agent 开始前的门控检查脚本。
所有检查通过后才可以开始 P0-3 实施。

用途：验证 db_tags.py 中 ensure_tag 存在、
      get_relevant_memory_tags SQL 包含 pinned 类型、
      memory.py 可正常导入。

用法：python scripts/check_memory_gate.py
"""
import sys


def check_ensure_tag_exists():
    """检查 ensure_tag 函数是否在 db_tags.py 中定义（HF-3 修复）。"""
    try:
        from plugins.deepseek.db_tags import ensure_tag
        print("✅ Gate-1a: ensure_tag 存在")
        return True
    except ImportError:
        print("❌ Gate-1a FAILED: db_tags.py 中 ensure_tag 未定义，请先完成 HF-3 热修复")
        return False


def check_pinned_in_sql():
    """检查 get_relevant_memory_tags 的 SQL 是否包含 'pinned' 类型。"""
    import inspect
    from plugins.deepseek.db_tags import get_relevant_memory_tags
    src = inspect.getsource(get_relevant_memory_tags)
    if "'pinned'" in src or '"pinned"' in src:
        print("✅ Gate-1b: get_relevant_memory_tags SQL 已包含 pinned 类型")
        return True
    else:
        print("❌ Gate-1b FAILED: get_relevant_memory_tags SQL 未查询 pinned 类型记忆")
        return False


def check_memory_module_imports():
    """检查 memory.py 可以正常 import（无 NameError）。"""
    try:
        import plugins.deepseek.memory  # noqa
        print("✅ Gate-1c: memory.py import 正常")
        return True
    except Exception as e:
        print(f"❌ Gate-1c FAILED: memory.py import 失败: {e}")
        return False


def main():
    checks = [
        check_ensure_tag_exists,
        check_pinned_in_sql,
        check_memory_module_imports,
    ]
    results = []
    for c in checks:
        try:
            results.append(c())
        except Exception as e:
            print(f"❌ 检查 {c.__name__} 执行异常: {e}")
            results.append(False)

    print()
    if all(results):
        print("🎉 所有门控检查通过，可以开始 P0-3 Memory Agent 实施。")
        sys.exit(0)
    else:
        failed = sum(1 for r in results if not r)
        print(f"🚫 {failed} 项检查未通过，请先完成上述修复再开始 P0-3。")
        sys.exit(1)


if __name__ == "__main__":
    main()
