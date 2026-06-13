"""用户偏好 + 回复质量 — 测试。"""
import pytest
import asyncio
import tempfile
import os

import plugins.deepseek.db_core as db_core_mod
pytestmark = [pytest.mark.unit, pytest.mark.needs_db]



async def _fresh_db():
    """关闭旧连接，创建新的临时数据库，返回 database 模块。"""
    if db_core_mod._db:
        try:
            await db_core_mod._db.close()
        except Exception:
            pass
    db_core_mod._db = None
    # 每次用新的临时文件，避免文件锁冲突
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    db_core_mod.DB_PATH = path
    from plugins.deepseek import database
    await database.init_db()
    return database, path


async def _cleanup(path):
    """关闭连接并清理临时文件。"""
    if db_core_mod._db:
        try:
            await db_core_mod._db.close()
        except Exception:
            pass
    db_core_mod._db = None
    try:
        os.remove(path)
    except OSError:
        pass


class TestUserPreferencesDB:
    """测试 user_preferences 数据库操作。"""

    @pytest.mark.asyncio
    async def test_update_and_get_preference(self):
        db, path = await _fresh_db()
        try:
            await db.update_user_preference("user1", "reply_length", "long", 0.3)
            prefs = await db.get_user_preferences("user1")
            assert "reply_length" in prefs
            assert prefs["reply_length"]["long"] == 0.3
        finally:
            await _cleanup(path)

    @pytest.mark.asyncio
    async def test_preference_accumulates(self):
        db, path = await _fresh_db()
        try:
            await db.update_user_preference("user1", "reply_length", "long", 0.3)
            await db.update_user_preference("user1", "reply_length", "long", 0.2)
            prefs = await db.get_user_preferences("user1")
            assert prefs["reply_length"]["long"] == pytest.approx(0.5, abs=0.01)
        finally:
            await _cleanup(path)

    @pytest.mark.asyncio
    async def test_preference_capped_at_1(self):
        db, path = await _fresh_db()
        try:
            await db.update_user_preference("user1", "sticker_freq", "high", 0.6)
            await db.update_user_preference("user1", "sticker_freq", "high", 0.6)
            prefs = await db.get_user_preferences("user1")
            assert prefs["sticker_freq"]["high"] <= 1.0
        finally:
            await _cleanup(path)

    @pytest.mark.asyncio
    async def test_get_top_preference(self):
        db, path = await _fresh_db()
        try:
            await db.update_user_preference("user1", "reply_length", "short", 0.2)
            await db.update_user_preference("user1", "reply_length", "long", 0.5)
            top = await db.get_top_preference("user1", "reply_length")
            assert top == "long"
        finally:
            await _cleanup(path)


class TestReplyQualityDB:
    """测试 reply_quality 数据库操作。"""

    @pytest.mark.asyncio
    async def test_save_and_get_quality_stats(self):
        db, path = await _fresh_db()
        try:
            await db.save_reply_quality("user1", "sess1", "hello", 1.0, "emoji_reaction")
            await db.save_reply_quality("user1", "sess1", "what", -1.0, "confusion")
            await db.save_reply_quality("user1", "sess1", "ok", 0.0, "neutral")
            stats = await db.get_quality_stats("user1", days=1)
            assert stats["total"] == 3
            assert stats["avg_score"] == pytest.approx(0.0, abs=0.01)
            assert stats["confusion_rate"] == pytest.approx(1 / 3, abs=0.01)
        finally:
            await _cleanup(path)

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        db, path = await _fresh_db()
        try:
            stats = await db.get_quality_stats("nobody", days=7)
            assert stats["total"] == 0
            assert stats["avg_score"] == 0
        finally:
            await _cleanup(path)


class TestPromptUserPrefs:
    """测试 prompt 集成用户偏好。"""

    def test_pref_hint_in_prompt(self):
        from plugins.deepseek.prompt import build_system_prompt
        result = build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs={"reply_length": "long", "topic_interest": "游戏"},
        )
        assert "喜欢详细回复" in result
        assert "游戏" in result

    def test_no_prefs_no_hint(self):
        from plugins.deepseek.prompt import build_system_prompt
        result = build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs=None,
        )
        assert "用户偏好" not in result

    def test_sticker_high_pref(self):
        from plugins.deepseek.prompt import build_system_prompt
        result = build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs={"sticker_freq": "high"},
        )
        assert "多发表情包" in result
