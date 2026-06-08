"""数据库迁移机制测试。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import pytest
from plugins.deepseek.migrations import (
    MIGRATIONS, ensure_migration_table, get_current_version, run_migrations
)

pytestmark = [pytest.mark.integration, pytest.mark.needs_db]


class TestMigrations:
    @pytest.mark.asyncio
    async def test_migration_table_created(self):
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        await ensure_migration_table(db)
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='_migrations'") as cur:
            row = await cur.fetchone()
            assert row is not None
        await db.close()

    @pytest.mark.asyncio
    async def test_initial_version_zero(self):
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        version = await get_current_version(db)
        assert version == 0
        await db.close()

    def test_migrations_registered(self):
        assert len(MIGRATIONS) >= 3
        versions = [v for v, _ in MIGRATIONS]
        assert 1 in versions
        assert 2 in versions
        assert 3 in versions

    @pytest.mark.asyncio
    async def test_run_migrations(self):
        import aiosqlite
        db = await aiosqlite.connect(":memory:")
        await db.execute("""
            CREATE TABLE IF NOT EXISTS memory_tags (
                id INTEGER PRIMARY KEY, user_id TEXT, tag_type TEXT, content TEXT,
                weight REAL, created_at REAL, last_used REAL
            )
        """)
        await db.commit()
        await run_migrations(db)
        version = await get_current_version(db)
        assert version >= 3
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='session_state'") as cur:
            row = await cur.fetchone()
            assert row is not None
        # v3: user_preferences 和 reply_quality 表
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_preferences'") as cur:
            row = await cur.fetchone()
            assert row is not None
        async with db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='reply_quality'") as cur:
            row = await cur.fetchone()
            assert row is not None
        await db.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
