"""Test database facade — 数据库外观模式集成测试。

H-11: 使用 :memory: 数据库进行集成测试，覆盖 database.py 外观接口。
"""
import pytest
import asyncio

pytestmark = [pytest.mark.integration, pytest.mark.needs_db]
# 辅助：确保数据库表已初始化
# ═══════════════════════════════════════════════════════════════

_DB_INITIALIZED = False


async def _ensure_db_ready():
    global _DB_INITIALIZED
    if not _DB_INITIALIZED:
        from plugins.deepseek.database import init_db
        await init_db()
        _DB_INITIALIZED = True


# ═══════════════════════════════════════════════════════════════
# 数据库初始化
# ═══════════════════════════════════════════════════════════════

class TestDatabaseInit:
    """测试数据库初始化。"""

    @pytest.mark.asyncio
    async def test_get_db_returns_connection(self):
        """get_db 应返回有效的数据库连接。"""
        from plugins.deepseek.db_core import get_db
        db = await get_db()
        assert db is not None

    @pytest.mark.asyncio
    async def test_db_execute_query(self):
        """数据库应能执行基本查询。"""
        from plugins.deepseek.db_core import get_db
        db = await get_db()
        async with db.execute("SELECT 1") as cur:
            row = await cur.fetchone()
            assert row[0] == 1

    @pytest.mark.asyncio
    async def test_db_checkpoint(self):
        """checkpoint 不应抛异常。"""
        from plugins.deepseek.db_core import checkpoint_db
        await checkpoint_db()
        # 不应抛异常


# ═══════════════════════════════════════════════════════════════
# WAL / PRAGMA 配置
# ═══════════════════════════════════════════════════════════════

class TestPragmaSettings:
    """测试数据库 PRAGMA 设置。"""

    @pytest.mark.asyncio
    async def test_wal_mode_enabled(self):
        """WAL 模式应已启用（:memory: 数据库为 MEMORY 模式除外）。"""
        from plugins.deepseek.db_core import get_db
        db = await get_db()
        async with db.execute("PRAGMA journal_mode") as cur:
            row = await cur.fetchone()
            # :memory: 数据库返回 'memory'，文件数据库返回 'wal'
            assert row[0].upper() in ("WAL", "MEMORY")

    @pytest.mark.asyncio
    async def test_foreign_keys_enabled(self):
        """外键约束应已启用。"""
        from plugins.deepseek.db_core import get_db
        db = await get_db()
        async with db.execute("PRAGMA foreign_keys") as cur:
            row = await cur.fetchone()
            assert row[0] == 1


# ═══════════════════════════════════════════════════════════════
# 用户 Profile 操作
# ═══════════════════════════════════════════════════════════════

class TestUserProfile:
    """测试用户 profile 操作。"""

    @pytest.mark.asyncio
    async def test_get_or_create_user_profile(self):
        """获取或创建用户 profile。"""
        await _ensure_db_ready()
        from plugins.deepseek.db_session import get_or_create_user_profile
        profile = await get_or_create_user_profile("test_user_001")
        assert profile["user_id"] == "test_user_001"
        assert "relationship_style" in profile

    @pytest.mark.asyncio
    async def test_update_user_profile_valid_fields(self):
        """使用有效字段更新 profile。"""
        await _ensure_db_ready()
        from plugins.deepseek.db_session import update_user_profile, get_or_create_user_profile
        await get_or_create_user_profile("test_user_002")
        await update_user_profile("test_user_002", nickname="测试昵称")
        profile = await get_or_create_user_profile("test_user_002")
        assert profile["nickname"] == "测试昵称"

    @pytest.mark.asyncio
    async def test_update_user_profile_invalid_fields_ignored(self):
        """无效字段应被忽略（白名单防护）。"""
        await _ensure_db_ready()
        from plugins.deepseek.db_session import update_user_profile, get_or_create_user_profile
        await get_or_create_user_profile("test_user_003")
        # 尝试注入无效列名（应被白名单拦截）
        await update_user_profile("test_user_003", malicious_column="DROP TABLE users")
        # 不应抛异常，profile 应正常
        profile = await get_or_create_user_profile("test_user_003")
        assert "malicious_column" not in profile


# ═══════════════════════════════════════════════════════════════
# 记忆操作
# ═══════════════════════════════════════════════════════════════

class TestMemories:
    """测试记忆操作。"""

    @pytest.mark.asyncio
    async def test_archive_memories_except_empty(self):
        """空 keep_ids 时 archive_memories_except 应安全返回。"""
        await _ensure_db_ready()
        from plugins.deepseek.db_memories import archive_memories_except
        # 空列表不应抛异常（函数内直接 return）
        await archive_memories_except("test_session", [])

    @pytest.mark.asyncio
    async def test_has_recent_message(self):
        """has_recent_message 应对新会话返回 False。"""
        await _ensure_db_ready()
        from plugins.deepseek.db_memories import has_recent_message
        result = await has_recent_message("nonexistent_session")
        assert result is False
