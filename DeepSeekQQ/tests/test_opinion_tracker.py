# -*- coding: utf-8 -*-
"""opinion_tracker tests — 意见追踪和立场一致性。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]


class TestRecordOpinion:
    @pytest.mark.asyncio
    async def test_record_returns_true(self):
        """记录立场返回 True。"""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_cursor = AsyncMock()
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_db.execute.return_value = mock_cursor

        with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
            from plugins.deepseek.opinion_tracker import record_opinion
            result = await record_opinion("test_user", "奶茶", "我喜欢奶茶")
            assert result is True

    @pytest.mark.asyncio
    async def test_record_fails_gracefully(self):
        """数据库异常时返回 False 不抛异常。"""
        mock_db = MagicMock()
        mock_db.execute = AsyncMock(side_effect=Exception("DB error"))

        with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
            from plugins.deepseek.opinion_tracker import record_opinion
            result = await record_opinion("test_user", "奶茶", "我喜欢奶茶")
            assert result is False


class TestGetPastOpinions:
    @pytest.mark.asyncio
    async def test_returns_list(self):
        """查询历史立场返回列表。"""
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_db.execute.return_value = mock_cursor

        with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
            from plugins.deepseek.opinion_tracker import get_past_opinions
            results = await get_past_opinions("test_user")
            assert isinstance(results, list)

    @pytest.mark.asyncio
    async def test_empty_for_new_user(self):
        """新用户无历史立场。"""
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_db.execute.return_value = mock_cursor

        with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
            from plugins.deepseek.opinion_tracker import get_past_opinions
            results = await get_past_opinions("new_user")
            assert results == []


class TestBuildPastOpinionsHint:
    def test_empty_for_no_opinions(self):
        """无历史立场返回空。"""
        from plugins.deepseek.opinion_tracker import build_past_opinions_hint
        result = build_past_opinions_hint([])
        assert result == ""

    def test_formats_opinions_correctly(self):
        """格式化历史立场为提示文本。"""
        from plugins.deepseek.opinion_tracker import build_past_opinions_hint
        opinions = [
            {"topic": "奶茶", "bot_stance": "我喜欢奶茶", "mention_count": 1},
            {"topic": "早起", "bot_stance": "早起毁一天", "mention_count": 5},
        ]
        result = build_past_opinions_hint(opinions)
        assert "奶茶" in result
        assert "早起" in result
        assert "【你之前表达过的观点" in result


class TestGetTopicOpinion:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        """不存在的话题返回 None。"""
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchone = AsyncMock(return_value=None)
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_db.execute.return_value = mock_cursor

        with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
            from plugins.deepseek.opinion_tracker import get_topic_opinion
            result = await get_topic_opinion("test_user", "不存在")
            assert result is None


class TestEvolveOpinion:
    @pytest.mark.asyncio
    async def test_returns_false_for_low_affection(self):
        """低好感度不触发立场演化。"""
        from plugins.deepseek.opinion_tracker import evolve_opinion
        result = await evolve_opinion("test_user", "奶茶", "新立场", affection_score=100)
        assert result is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
