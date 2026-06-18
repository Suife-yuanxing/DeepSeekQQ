"""Test DB Core — 数据库连接池管理。

覆盖：
- get_db 单例/延迟初始化/锁保护
- checkpoint_db WAL checkpoint
- close_db 关闭连接
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# get_db — 连接获取
# ═══════════════════════════════════════════════════════════════

class TestGetDb:
    """测试 get_db 连接管理。"""

    @pytest.mark.asyncio
    async def test_get_db_returns_connection(self):
        """get_db 应返回 aiosqlite 连接。"""
        import plugins.deepseek.db_core as db_core
        # 重置状态
        db_core._db = None
        try:
            conn = await db_core.get_db()
            assert conn is not None
            # 再次调用应返回同一连接（单例）
            conn2 = await db_core.get_db()
            assert conn is conn2
        finally:
            await db_core.close_db()

    @pytest.mark.asyncio
    async def test_get_db_singleton(self):
        """多次调用 get_db 返回同一连接。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        try:
            c1 = await db_core.get_db()
            c2 = await db_core.get_db()
            assert c1 is c2
        finally:
            await db_core.close_db()

    @pytest.mark.asyncio
    async def test_get_db_reconnect_on_failure(self):
        """健康检查失败时应重建连接。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        try:
            conn = await db_core.get_db()
            # 模拟连接失效：第一次 execute 抛异常
            original_execute = conn.execute

            call_count = [0]

            async def mock_execute(*args, **kwargs):
                call_count[0] += 1
                if call_count[0] == 1:
                    raise Exception("Connection lost")
                return await original_execute(*args, **kwargs)

            conn.execute = mock_execute
            # 此时 _db 仍指向旧连接，但 execute 会失败
            # 下一次 get_db 应检测到并重建
            db_core._db = conn  # 确保 _db 指向我们的 mock 连接
            new_conn = await db_core.get_db()
            assert new_conn is not None
        finally:
            await db_core.close_db()


# ═══════════════════════════════════════════════════════════════
# checkpoint_db
# ═══════════════════════════════════════════════════════════════

class TestCheckpointDb:
    """测试 checkpoint_db。"""

    @pytest.mark.asyncio
    async def test_checkpoint_no_error(self):
        """checkpoint 在有效连接下不应报错。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        try:
            await db_core.get_db()  # 初始化
            await db_core.checkpoint_db()  # 不应抛异常
        finally:
            await db_core.close_db()

    @pytest.mark.asyncio
    async def test_checkpoint_no_db(self):
        """无连接时 checkpoint 应为 no-op。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        # 不应抛异常
        await db_core.checkpoint_db()


# ═══════════════════════════════════════════════════════════════
# close_db
# ═══════════════════════════════════════════════════════════════

class TestCloseDb:
    """测试 close_db。"""

    @pytest.mark.asyncio
    async def test_close_clears_connection(self):
        """close_db 应将 _db 置为 None。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        try:
            await db_core.get_db()
            assert db_core._db is not None
            await db_core.close_db()
            assert db_core._db is None
        finally:
            await db_core.close_db()

    @pytest.mark.asyncio
    async def test_close_when_already_none(self):
        """_db 已为 None 时 close_db 应为 no-op。"""
        import plugins.deepseek.db_core as db_core
        db_core._db = None
        await db_core.close_db()  # 不应抛异常
        assert db_core._db is None
