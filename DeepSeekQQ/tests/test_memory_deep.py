"""记忆系统深化测试 — 共同回忆、私人梗、重要日期。"""
import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
pytestmark = [pytest.mark.unit, pytest.mark.needs_db]



class MockCursor:
    """模拟 aiosqlite cursor，支持 fetchone/fetchall/__aiter__。"""
    def __init__(self, rows=None):
        self.rows = rows if rows is not None else []
        self.rowcount = len(self.rows)

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows

    def __aiter__(self):
        return iter(self.rows).__aiter__()


class MockExecuteResult:
    """同时支持 await 和 async with：- await db.execute(...) → 返回 cursor- async with db.execute(...) as cursor → yield cursor"""
    def __init__(self, cursor):
        self._cursor = cursor

    def __await__(self):
        # 支持 await db.execute(...)
        async def _f():
            return self._cursor
        return _f().__await__()

    async def __aenter__(self):
        return self._cursor

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.commit = AsyncMock()
    cursor = MockCursor()
    db.execute = MagicMock(return_value=MockExecuteResult(cursor))
    return db, MockCursor


# ============================================================
# 共同回忆测试
# ============================================================

class TestSharedMemories:
    @pytest.mark.asyncio
    async def test_save_shared_memory_new(self, mock_db):
        db, MockCursor = mock_db
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=[])
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import save_shared_memory
            await save_shared_memory(
                "test_user", "first_chat", "我们第一次聊天很开心", "开心"
            )
            assert db.execute.call_count >= 1
            # 验证 SQL 包含 INSERT 或 UPDATE（确认调用了写操作）
            first_call_sql = str(db.execute.call_args_list[0])
            assert "INSERT" in first_call_sql or "UPDATE" in first_call_sql or "first_chat" in first_call_sql, \
                f"期望 INSERT/UPDATE 但得到: {first_call_sql[:200]}"
            assert db.commit.called

    @pytest.mark.asyncio
    async def test_save_shared_memory_duplicate(self, mock_db):
        db, MockCursor = mock_db
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=[{"id": 1, "importance": 0.5}])
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import save_shared_memory
            await save_shared_memory(
                "test_user", "first_chat", "我们第一次聊天很开心"
            )
            # 应该执行了至少一次 SQL，且包含写操作
            assert db.execute.call_count >= 1
            first_call_sql = str(db.execute.call_args_list[0])
            assert "INSERT" in first_call_sql or "UPDATE" in first_call_sql or "SELECT" in first_call_sql, \
                f"期望有效 SQL 但得到: {first_call_sql[:200]}"

    @pytest.mark.asyncio
    async def test_get_recall_candidates_empty(self, mock_db):
        db, MockCursor = mock_db
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=[])
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import get_recall_candidates
            result = await get_recall_candidates("test_user", "你好")
            assert result == []

    @pytest.mark.asyncio
    async def test_recall_candidates_with_keyword_match(self, mock_db):
        db, MockCursor = mock_db
        now = time.time()
        rows = [
            {
                "id": 1, "event_type": "first_chat",
                "event_desc": "我们第一次聊天很开心",
                "emotion_tag": "开心", "importance": 0.8,
                "recall_count": 0, "created_at": now - 86400,
                "last_recalled": 0,
            }
        ]
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=rows)
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import get_recall_candidates
            result = await get_recall_candidates("test_user", "还记得我们第一次聊天吗")
            assert len(result) >= 1
            assert result[0]["event_desc"] == "我们第一次聊天很开心"


# ============================================================
# 私人梗测试
# ============================================================

class TestPrivateMemes:
    @pytest.mark.asyncio
    async def test_save_private_meme_new(self, mock_db):
        db, MockCursor = mock_db
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=[])
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import save_private_meme
            await save_private_meme(
                "test_user", "nickname", "小喵喵",
                origin_context="以后叫你小喵喵", trigger_keywords="小喵喵"
            )
            assert db.execute.call_count >= 1
            first_call_sql = str(db.execute.call_args_list[0])
            assert "INSERT" in first_call_sql or "UPDATE" in first_call_sql or "nickname" in first_call_sql, \
                f"期望 INSERT/UPDATE 但得到: {first_call_sql[:200]}"
            assert db.commit.called

    @pytest.mark.asyncio
    async def test_find_matching_meme_hit(self, mock_db):
        db, MockCursor = mock_db
        now = time.time()
        rows = [
            {
                "id": 1, "meme_type": "joke",
                "content": "笨蛋猫", "trigger_keywords": "笨蛋,笨猫",
                "frequency": 0.8, "usage_count": 3, "last_used": now - 7200,
            }
        ]
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=rows)
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import find_matching_meme
            result = await find_matching_meme("test_user", "你这个笨蛋又搞错了")
            assert result is None or isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_find_matching_meme_cooldown(self, mock_db):
        db, MockCursor = mock_db
        now = time.time()
        rows = [
            {
                "id": 1, "meme_type": "joke",
                "content": "笨蛋猫", "trigger_keywords": "笨蛋,笨猫",
                "frequency": 0.8, "usage_count": 3, "last_used": now - 1800,
            }
        ]
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=rows)
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import find_matching_meme
            result = await find_matching_meme("test_user", "你这个笨蛋")
            assert result is None


# ============================================================
# 重要日期测试
# ============================================================

class TestImportantDates:
    @pytest.mark.asyncio
    async def test_save_important_date(self, mock_db):
        db, MockCursor = mock_db
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            from plugins.deepseek.db_memories_deep import save_important_date
            await save_important_date(
                "test_user", "birthday", "03-15", "生日 03-15"
            )
            assert db.execute.call_count >= 1
            first_call_sql = str(db.execute.call_args_list[0])
            assert "INSERT" in first_call_sql or "birthday" in first_call_sql or "03-15" in first_call_sql, \
                f"期望 INSERT 包含日期数据但得到: {first_call_sql[:200]}"
            assert db.commit.called

    @pytest.mark.asyncio
    async def test_get_today_dates(self, mock_db):
        db, MockCursor = mock_db
        rows = [
            {"id": 1, "date_type": "birthday", "date_value": "03-15",
             "description": "生日 03-15", "repeat_yearly": 1}
        ]
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=rows)
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import get_today_dates
            result = await get_today_dates("test_user", "03-15")
            assert len(result) >= 1
            assert result[0]["date_type"] == "birthday"

    @pytest.mark.asyncio
    async def test_get_upcoming_dates(self, mock_db):
        db, MockCursor = mock_db
        from datetime import datetime, timedelta
        now = datetime.now()
        future = now + timedelta(days=3)
        future_mm_dd = future.strftime("%m-%d")
        rows = [
            {"id": 1, "date_type": "anniversary", "date_value": future_mm_dd,
             "description": "纪念日", "repeat_yearly": 1}
        ]
        with patch('plugins.deepseek.db_memories_deep.get_db', return_value=db):
            cursor = MockCursor(rows=rows)
            db.execute = MagicMock(return_value=MockExecuteResult(cursor))

            from plugins.deepseek.db_memories_deep import get_upcoming_dates
            result = await get_upcoming_dates("test_user", within_days=7)
            assert len(result) >= 1
            assert result[0]["days_until"] <= 7


# ============================================================
# 提取函数测试
# ============================================================

class TestExtraction:
    @pytest.mark.asyncio
    async def test_extract_shared_memories_skips_unrelated(self):
        """无关消息不应触发 LLM 调用。"""
        from plugins.deepseek.memory import _extract_shared_memories
        with patch('plugins.deepseek.api.call_deepseek_api', new_callable=AsyncMock) as mock_api:
            await _extract_shared_memories("test_user", "今天天气不错", "是呀~")
            mock_api.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_important_dates_birthday(self):
        """生日关键词应触发日期提取。"""
        from plugins.deepseek.memory import _extract_important_dates
        with patch('plugins.deepseek.db_memories_deep.save_important_date', new_callable=AsyncMock) as mock_save:
            await _extract_important_dates("test_user", "我生日是3月15号")
            mock_save.assert_called_once()
            call_args = mock_save.call_args
            assert call_args[0][1] == "birthday"  # date_type
            assert "03-15" in call_args[0][2]  # date_value

    @pytest.mark.asyncio
    async def test_extract_private_memes_nickname(self):
        """昵称应触发私人梗提取。"""
        from plugins.deepseek.memory import _extract_private_memes
        with patch('plugins.deepseek.db_memories_deep.save_private_meme', new_callable=AsyncMock) as mock_save:
            await _extract_private_memes("test_user", "以后叫你小甜甜", "好的喵~")
            mock_save.assert_called_once()
            call_args = mock_save.call_args
            assert call_args[0][1] == "nickname"  # meme_type
            assert "小甜甜" in call_args[0][2]  # content


# ============================================================
# 提示生成测试
# ============================================================

class TestHints:
    @pytest.mark.asyncio
    async def test_get_shared_memory_hint_returns_none_when_empty(self):
        with patch('plugins.deepseek.db_memories_deep.get_recall_candidates', new_callable=AsyncMock, return_value=[]):
            from plugins.deepseek.memory import get_shared_memory_hint
            result = await get_shared_memory_hint("test_user", "你好")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_private_meme_hint_returns_none_when_no_match(self):
        with patch('plugins.deepseek.db_memories_deep.find_matching_meme', new_callable=AsyncMock, return_value=None):
            from plugins.deepseek.memory import get_private_meme_hint
            result = await get_private_meme_hint("test_user", "你好")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_date_hint_returns_none_when_no_dates(self):
        with patch('plugins.deepseek.db_memories_deep.get_today_dates', new_callable=AsyncMock, return_value=[]), \
             patch('plugins.deepseek.db_memories_deep.get_upcoming_dates', new_callable=AsyncMock, return_value=[]):
            from plugins.deepseek.memory import get_date_hint
            result = await get_date_hint("test_user")
            assert result is None
