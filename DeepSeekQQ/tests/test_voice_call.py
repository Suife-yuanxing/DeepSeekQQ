# -*- coding: utf-8 -*-
"""语音通话模式测试 — 状态机 + 意图检测 + should_send_voice 覆盖。"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

pytestmark = [pytest.mark.unit]


class TestDetectVoiceIntent:
    """测试意图检测 — detect_voice_intent()"""

    def test_enter_keywords(self):
        from plugins.deepseek.voice_call import detect_voice_intent
        for kw in ["打电话", "语音通话", "语音聊天", "通话", "开语音", "接电话"]:
            assert detect_voice_intent(kw) == "enter", f"关键词 '{kw}' 应返回 'enter'"

    def test_exit_keywords(self):
        from plugins.deepseek.voice_call import detect_voice_intent
        # Bug 8 修复：移除了 "挂了"（太容易误触发），测试新词表
        for kw in ["挂断", "不打了", "挂电话了", "挂了吧", "结束通话", "挂电话"]:
            assert detect_voice_intent(kw) == "exit", f"关键词 '{kw}' 应返回 'exit'"

    def test_guale_no_longer_triggers_exit(self):
        """Bug 8 回归：单字组合 '挂了' 不再触发退出（防止误触发）。"""
        from plugins.deepseek.voice_call import detect_voice_intent
        assert detect_voice_intent("挂了") is None, "'挂了' 不应再单独触发退出"

    def test_normal_message_returns_none(self):
        from plugins.deepseek.voice_call import detect_voice_intent
        for msg in ["你好", "今天天气怎么样", "在干嘛", "我喜欢你", ""]:
            assert detect_voice_intent(msg) is None, f"'{msg}' 不应触发语音意图"

    def test_partial_match_not_triggered(self):
        from plugins.deepseek.voice_call import detect_voice_intent
        # "挂" 单独不触发，"挂了"才触发
        assert detect_voice_intent("挂") is None
        # "电" 单独不触发
        assert detect_voice_intent("电") is None
        # "打电话" 触发
        assert detect_voice_intent("打电话") == "enter"

    def test_embedded_keyword(self):
        from plugins.deepseek.voice_call import detect_voice_intent
        # 关键词在消息中间也应该检测到
        assert detect_voice_intent("我们打电话聊吧") == "enter"
        # 退出在包含文本中
        assert detect_voice_intent("好了先挂了吧再见") == "exit"


class TestVoiceCallState:
    """测试语音通话状态机。"""

    @pytest.mark.asyncio
    async def test_enter_voice_mode(self):
        from plugins.deepseek.voice_call import enter_voice_mode
        from plugins.deepseek.voice_call import is_in_voice_mode
        from plugins.deepseek.voice_call import exit_voice_mode

        sid = "test_session_1"
        state = enter_voice_mode(sid)
        assert state.active is True
        assert state.started_at > 0
        assert state.last_activity > 0
        assert is_in_voice_mode(sid) is True
        # 清理：退出避免超时任务残留
        exit_voice_mode(sid)

    @pytest.mark.asyncio
    async def test_exit_voice_mode(self):
        from plugins.deepseek.voice_call import enter_voice_mode
        from plugins.deepseek.voice_call import exit_voice_mode
        from plugins.deepseek.voice_call import is_in_voice_mode

        sid = "test_session_2"
        enter_voice_mode(sid)
        assert is_in_voice_mode(sid) is True

        result = exit_voice_mode(sid)
        assert result is True
        assert is_in_voice_mode(sid) is False

    def test_exit_not_active_returns_false(self):
        from plugins.deepseek.voice_call import exit_voice_mode
        result = exit_voice_mode("non_existent_session")
        assert result is False

    @pytest.mark.asyncio
    async def test_touch_activity_updates_timestamp(self):
        import asyncio
        from plugins.deepseek.voice_call import enter_voice_mode
        from plugins.deepseek.voice_call import exit_voice_mode
        from plugins.deepseek.voice_call import touch_activity

        sid = "test_session_3"
        state = enter_voice_mode(sid)
        original_ts = state.last_activity

        # 短暂延迟确保时间戳不同
        await asyncio.sleep(0.01)
        touch_activity(sid)
        assert state.last_activity > original_ts
        exit_voice_mode(sid)

    @pytest.mark.asyncio
    async def test_enter_existing_updates_activity(self):
        import asyncio
        from plugins.deepseek.voice_call import enter_voice_mode
        from plugins.deepseek.voice_call import exit_voice_mode

        sid = "test_session_4"
        state1 = enter_voice_mode(sid)
        ts1 = state1.last_activity

        await asyncio.sleep(0.01)
        state2 = enter_voice_mode(sid)  # 再次进入
        assert state2 is state1  # 同一个对象
        assert state2.last_activity > ts1
        exit_voice_mode(sid)


class TestShouldSendVoiceOverride:
    """测试 should_send_voice 的 voice_mode 参数。"""

    def test_voice_mode_always_true(self):
        from plugins.deepseek.voice import should_send_voice
        result = should_send_voice("hello", "long reply text here", [], voice_mode=True)
        assert result is True

    def test_voice_mode_false_uses_normal_logic(self):
        from plugins.deepseek.voice import should_send_voice
        # voice_mode=False，正常逻辑：低概率 + 长文本 → 通常 False
        result = should_send_voice("normal message", "a" * 200, [], voice_mode=False)
        # 长文本会返回 False（超过 VOICE_MAX_LENGTH=120）
        assert result is False

    def test_voice_mode_false_test_keyword(self):
        from plugins.deepseek.voice import should_send_voice
        # "语音测试" 关键词即使 voice_mode=False 也返回 True
        result = should_send_voice("语音测试", "reply", [], voice_mode=False)
        assert result is True
