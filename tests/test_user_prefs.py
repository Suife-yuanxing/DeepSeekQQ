"""功能③⑦：用户偏好 + 回复质量 — 测试。"""
import pytest
import asyncio
import os

# 使用内存数据库测试
os.environ.setdefault("DEEPSEEK_DB_PATH", ":memory:")


class TestUserPreferencesDB:
    """测试 user_preferences 数据库操作。"""

    @pytest.mark.asyncio
    async def test_update_and_get_preference(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            await database.update_user_preference("user1", "reply_length", "long", 0.3)
            prefs = await database.get_user_preferences("user1")
            assert "reply_length" in prefs
            assert prefs["reply_length"]["long"] == 0.3
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None

    @pytest.mark.asyncio
    async def test_preference_accumulates(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            await database.update_user_preference("user1", "reply_length", "long", 0.3)
            await database.update_user_preference("user1", "reply_length", "long", 0.2)
            prefs = await database.get_user_preferences("user1")
            assert prefs["reply_length"]["long"] == pytest.approx(0.5, abs=0.01)
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None

    @pytest.mark.asyncio
    async def test_preference_capped_at_1(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            await database.update_user_preference("user1", "sticker_freq", "high", 0.6)
            await database.update_user_preference("user1", "sticker_freq", "high", 0.6)
            prefs = await database.get_user_preferences("user1")
            assert prefs["sticker_freq"]["high"] <= 1.0
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None

    @pytest.mark.asyncio
    async def test_get_top_preference(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            await database.update_user_preference("user1", "reply_length", "short", 0.2)
            await database.update_user_preference("user1", "reply_length", "long", 0.5)
            top = await database.get_top_preference("user1", "reply_length")
            assert top == "long"
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None


class TestReplyQualityDB:
    """测试 reply_quality 数据库操作。"""

    @pytest.mark.asyncio
    async def test_save_and_get_quality_stats(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            await database.save_reply_quality("user1", "sess1", "hello", 1.0, "emoji_reaction")
            await database.save_reply_quality("user1", "sess1", "what", -1.0, "confusion")
            await database.save_reply_quality("user1", "sess1", "ok", 0.0, "neutral")
            stats = await database.get_quality_stats("user1", days=1)
            assert stats["total"] == 3
            assert stats["avg_score"] == pytest.approx(0.0, abs=0.01)
            assert stats["confusion_rate"] == pytest.approx(1 / 3, abs=0.01)
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None

    @pytest.mark.asyncio
    async def test_empty_stats(self):
        from plugins.deepseek import database
        import plugins.deepseek.database as db_mod
        db_mod._db = None
        try:
            await database.init_db()
            stats = await database.get_quality_stats("nobody", days=7)
            assert stats["total"] == 0
            assert stats["avg_score"] == 0
        finally:
            if db_mod._db:
                await db_mod._db.close()
                db_mod._db = None


class TestPromptUserPrefs:
    """测试 prompt 集成用户偏好。"""

    def test_pref_hint_in_prompt(self):
        from plugins.deepseek.prompt import _build_system_prompt
        result = _build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs={"reply_length": "long", "topic_interest": "游戏"},
        )
        assert "喜欢详细回复" in result
        assert "游戏" in result

    def test_no_prefs_no_hint(self):
        from plugins.deepseek.prompt import _build_system_prompt
        result = _build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs=None,
        )
        assert "用户偏好" not in result

    def test_sticker_high_pref(self):
        from plugins.deepseek.prompt import _build_system_prompt
        result = _build_system_prompt(
            affection={"score": 0, "level": 1, "title": "陌生人"},
            mood={"mood": "平淡", "score": 50},
            length={"target_lines": 2, "style": "自然闲聊"},
            user_prefs={"sticker_freq": "high"},
        )
        assert "多发表情包" in result
