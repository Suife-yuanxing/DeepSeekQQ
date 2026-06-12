# -*- coding: utf-8 -*-
"""personality_drift tests — interest drift detection and catchphrase learning."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from collections import Counter

pytestmark = [pytest.mark.unit]


class TestTopicFrequency:
    @pytest.mark.asyncio
    async def test_get_frequency_returns_counter(self):
        mock_db = MagicMock()
        mock_db.execute = MagicMock()
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[])
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_db.execute.return_value = mock_cursor

        with patch('plugins.deepseek.personality._topic_prefs_cache', [
            {"content": "cat", "type": "topic_love"},
        ], create=True):
            with patch('plugins.deepseek.database.get_db', new=AsyncMock(return_value=mock_db)):
                from plugins.deepseek.personality_drift import get_recent_topic_frequency
                freq = await get_recent_topic_frequency("test_user")
                assert isinstance(freq, Counter)


class TestDriftHints:
    @pytest.mark.asyncio
    async def test_get_drift_hints_addiction(self):
        mock_counter = Counter({"cat": 15, "game": 3})

        with patch('plugins.deepseek.personality_drift.get_recent_topic_frequency',
                   new=AsyncMock(return_value=mock_counter)):
            with patch('plugins.deepseek.personality._topic_prefs_cache', [
                {"content": "cat", "type": "topic_love"},
            ], create=True):
                from plugins.deepseek.personality_drift import get_personality_drift_hints
                hints = await get_personality_drift_hints("test_user")
                assert len(hints) >= 1, f"Expected addiction hints, got: {hints}"
                assert any("cat" in h for h in hints)

    @pytest.mark.asyncio
    async def test_get_drift_hints_empty_for_no_data(self):
        mock_counter = Counter()
        with patch('plugins.deepseek.personality_drift.get_recent_topic_frequency',
                   new=AsyncMock(return_value=mock_counter)):
            with patch('plugins.deepseek.personality._topic_prefs_cache', [
                {"content": "cat", "type": "topic_love"},
            ], create=True):
                with patch('plugins.deepseek.personality_drift.random.random', return_value=0.99):
                    from plugins.deepseek.personality_drift import get_personality_drift_hints
                    hints = await get_personality_drift_hints("test_user")
                    assert isinstance(hints, list)


class TestCatchphraseLearning:
    @pytest.mark.asyncio
    async def test_low_affection_returns_none(self):
        from plugins.deepseek.personality_drift import maybe_learn_catchphrase
        result = await maybe_learn_catchphrase("test_user", 100)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
