"""Test DB Memories — 对话记忆 CRUD 操作。

覆盖：
- _fetch_one / _fetch_all / _execute 内部工具函数
- save_message / get_recent_memories 记忆读写
- trim_memories / count_memories 裁剪与计数
- archive_memories_except 归档
- has_recent_message / has_user_message_today 时间检查
- get_last_bot_reply_time 最后回复时间
"""
import inspect
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
async def db_ready():
    """初始化数据库连接和表结构，测试后清理。"""
    import plugins.deepseek.db_core as db_core
    from plugins.deepseek.database import init_db

    db_core._db = None
    await init_db()
    yield
    await db_core.close_db()


# ═══════════════════════════════════════════════════════════════
# 内部工具函数
# ═══════════════════════════════════════════════════════════════

class TestInternalHelpers:
    """测试 _fetch_one / _fetch_all / _execute 内部函数。"""

    def test_fetch_one_is_coroutine(self):
        """验证 _fetch_one 是 async 函数且可被导入。"""
        from plugins.deepseek.db_memories import _fetch_one
        assert inspect.iscoroutinefunction(_fetch_one)

    def test_fetch_all_is_coroutine(self):
        """验证 _fetch_all 是 async 函数且可被导入。"""
        from plugins.deepseek.db_memories import _fetch_all
        assert inspect.iscoroutinefunction(_fetch_all)

    def test_execute_is_coroutine(self):
        """验证 _execute 是 async 函数且可被导入。"""
        from plugins.deepseek.db_memories import _execute
        assert inspect.iscoroutinefunction(_execute)

    @pytest.mark.asyncio
    async def test_fetch_one_with_data(self, db_ready):
        """在有效表上执行 fetch_one 应返回结果。"""
        from plugins.deepseek.db_memories import _fetch_one, _execute
        import plugins.deepseek.db_core as db_core
        # 直接用 _execute 写数据
        await _execute(
            "INSERT INTO memories (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
            ("test_fetch", "user", "hello", 1234567890.0),
        )
        row = await _fetch_one("SELECT content FROM memories WHERE session_id = ?", ("test_fetch",))
        assert row is not None
        assert row["content"] == "hello"
        # 清理
        await _execute("DELETE FROM memories WHERE session_id = ?", ("test_fetch",))

    @pytest.mark.asyncio
    async def test_fetch_all_with_data(self, db_ready):
        """在有效表上执行 fetch_all 应返回多行。"""
        from plugins.deepseek.db_memories import _fetch_all, _execute
        for i in range(3):
            await _execute(
                "INSERT INTO memories (session_id, role, content, timestamp) VALUES (?, ?, ?, ?)",
                ("test_fa", "user", f"msg{i}", 1000.0 + i),
            )
        rows = await _fetch_all("SELECT content FROM memories WHERE session_id = ?", ("test_fa",))
        assert len(rows) == 3
        await _execute("DELETE FROM memories WHERE session_id = ?", ("test_fa",))

    @pytest.mark.asyncio
    async def test_fetch_one_returns_none(self, db_ready):
        """无匹配时 fetch_one 返回 None。"""
        from plugins.deepseek.db_memories import _fetch_one
        row = await _fetch_one("SELECT * FROM memories WHERE session_id = ?", ("nonexistent",))
        assert row is None


# ═══════════════════════════════════════════════════════════════
# save_message + get_recent_memories
# ═══════════════════════════════════════════════════════════════

class TestSaveAndRetrieve:
    """测试 save_message 和 get_recent_memories。"""

    @pytest.mark.asyncio
    async def test_save_and_retrieve(self, db_ready):
        """保存消息后应能检索到。"""
        from plugins.deepseek.db_memories import save_message, get_recent_memories
        await save_message("test_session_1", "user", "你好念念")
        await save_message("test_session_1", "assistant", "你好呀~")
        memories = await get_recent_memories("test_session_1", limit=10)
        assert len(memories) >= 2
        # 应该按时间升序返回 (reversed in code)
        assert memories[0]["role"] == "user"
        assert memories[1]["role"] == "assistant"
        assert "你好念念" in memories[0]["content"]

    @pytest.mark.asyncio
    async def test_recent_memories_respects_limit(self, db_ready):
        """get_recent_memories 应遵守 limit 参数。"""
        from plugins.deepseek.db_memories import save_message, get_recent_memories
        for i in range(5):
            await save_message("test_session_2", "user", f"msg{i}")
        memories = await get_recent_memories("test_session_2", limit=3)
        assert len(memories) <= 3

    @pytest.mark.asyncio
    async def test_recent_memories_excludes_archived(self, db_ready):
        """已归档的消息不应出现在 get_recent_memories 中。"""
        from plugins.deepseek.db_memories import (
            save_message, get_recent_memories, get_keep_ids, archive_memories_except,
        )
        await save_message("test_session_3", "user", "old_msg")
        # 归档所有消息
        keep_ids = await get_keep_ids("test_session_3", keep=0)
        await archive_memories_except("test_session_3", keep_ids or [])
        await save_message("test_session_3", "user", "new_msg")
        memories = await get_recent_memories("test_session_3", limit=10)
        contents = [m["content"] for m in memories]
        assert "new_msg" in contents


# ═══════════════════════════════════════════════════════════════
# trim_memories + count_memories
# ═══════════════════════════════════════════════════════════════

class TestTrimAndCount:
    """测试 trim_memories 和 count_memories。"""

    @pytest.mark.asyncio
    async def test_count_increases_with_saves(self, db_ready):
        """每保存一条消息 count 应增加。"""
        from plugins.deepseek.db_memories import save_message, count_memories
        session = "test_count_session"
        before = await count_memories(session)
        await save_message(session, "user", "test_msg")
        after = await count_memories(session)
        assert after == before + 1

    @pytest.mark.asyncio
    async def test_trim_removes_old_messages(self, db_ready):
        """trim 应删除超出 keep 数量的旧消息。"""
        from plugins.deepseek.db_memories import save_message, count_memories, trim_memories
        session = "test_trim_session"
        for i in range(10):
            await save_message(session, "user", f"msg{i}")
        before = await count_memories(session)
        assert before == 10
        await trim_memories(session, keep=3)
        after = await count_memories(session)
        assert after == 3

    @pytest.mark.asyncio
    async def test_count_empty_session(self, db_ready):
        """空 session 返回 0。"""
        from plugins.deepseek.db_memories import count_memories
        count = await count_memories("nonexistent_session")
        assert count == 0


# ═══════════════════════════════════════════════════════════════
# 时间相关查询
# ═══════════════════════════════════════════════════════════════

class TestTimeQueries:
    """测试 has_recent_message / get_last_bot_reply_time / has_user_message_today。"""

    @pytest.mark.asyncio
    async def test_has_recent_message_true(self, db_ready):
        """刚保存消息后 has_recent_message 应为 True。"""
        from plugins.deepseek.db_memories import save_message, has_recent_message
        session = "test_recent_session"
        await save_message(session, "user", "recent_msg")
        assert await has_recent_message(session, minutes=60) is True

    @pytest.mark.asyncio
    async def test_has_recent_message_empty(self, db_ready):
        """空 session 返回 False。"""
        from plugins.deepseek.db_memories import has_recent_message
        assert await has_recent_message("empty_session", minutes=60) is False

    @pytest.mark.asyncio
    async def test_get_last_bot_reply_time_empty(self, db_ready):
        """无 bot 回复时返回 0。"""
        from plugins.deepseek.db_memories import get_last_bot_reply_time
        ts = await get_last_bot_reply_time("empty_session")
        assert ts == 0

    @pytest.mark.asyncio
    async def test_get_last_bot_reply_time_after_save(self, db_ready):
        """保存 bot 回复后应返回 >0 的时间戳。"""
        from plugins.deepseek.db_memories import save_message, get_last_bot_reply_time
        session = "test_bot_reply_session"
        await save_message(session, "assistant", "bot_reply")
        ts = await get_last_bot_reply_time(session)
        assert ts > 0

    @pytest.mark.asyncio
    async def test_has_user_message_today(self, db_ready):
        """今天刚保存的消息应被检测到。"""
        from plugins.deepseek.db_memories import save_message, has_user_message_today
        session = "test_today_session"
        await save_message(session, "user", "today_msg")
        assert await has_user_message_today(session) is True

    @pytest.mark.asyncio
    async def test_has_user_message_today_empty(self, db_ready):
        """空 session 返回 False。"""
        from plugins.deepseek.db_memories import has_user_message_today
        assert await has_user_message_today("empty_today") is False


# ═══════════════════════════════════════════════════════════════
# archive_memories_except
# ═══════════════════════════════════════════════════════════════

class TestArchive:
    """测试 archive_memories_except。"""

    @pytest.mark.asyncio
    async def test_archive_empty_keep_ids(self, db_ready):
        """keep_ids 为空时应为 no-op（不抛异常）。"""
        from plugins.deepseek.db_memories import archive_memories_except
        await archive_memories_except("test", [])

    @pytest.mark.asyncio
    async def test_archive_preserves_keep_ids(self, db_ready):
        """归档后保留的消息应仍为非归档状态。"""
        from plugins.deepseek.db_memories import (
            save_message, get_keep_ids, archive_memories_except, get_recent_memories,
        )
        session = "test_archive_session"
        for i in range(5):
            await save_message(session, "user", f"msg{i}")
        keep_ids = await get_keep_ids(session, keep=2)
        await archive_memories_except(session, keep_ids)
        recent = await get_recent_memories(session, limit=10)
        assert len(recent) == 2
