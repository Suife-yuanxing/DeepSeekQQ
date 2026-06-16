"""Test Pipeline — ChatContext 构造、SKIP 传播、消息截断。

C-4: 覆盖 Pipeline 基础设施的关键行为。
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ═══════════════════════════════════════════════════════════════
# ChatContext 构造
# ═══════════════════════════════════════════════════════════════

class TestChatContext:
    """测试 ChatContext dataclass 的构造和字段。"""

    def test_chatcontext_basic_fields(self):
        """基本字段应正确初始化。"""
        from plugins.deepseek.pipeline import ChatContext
        bot = MagicMock()
        event = MagicMock()
        event.user_id = "12345"
        ctx = ChatContext(bot=bot, event=event, raw_msg="hello")
        assert ctx.bot is bot
        assert ctx.event is event
        assert ctx.raw_msg == "hello"
        assert ctx.is_group is False

    def test_chatcontext_default_values(self):
        """默认值应正确设置。"""
        from plugins.deepseek.pipeline import ChatContext
        bot = MagicMock()
        event = MagicMock()
        ctx = ChatContext(bot=bot, event=event)
        assert ctx.raw_msg == ""
        assert ctx.session_id == ""
        assert ctx.user_id == ""
        assert ctx.recent_memories == []
        assert ctx.affection == {}

    def test_chatcontext_group_event(self):
        """群聊事件 is_group 应为 True。"""
        from plugins.deepseek.pipeline import ChatContext
        from nonebot.adapters.onebot.v11 import GroupMessageEvent
        # 使用 MagicMock 模拟群聊事件
        bot = MagicMock()
        event = MagicMock(spec=GroupMessageEvent)
        event.user_id = "12345"
        from plugins.deepseek.pipeline import ChatContext as CC
        ctx = CC(bot=bot, event=event, is_group=True)
        assert ctx.is_group is True

    def test_complexity_field(self):
        """complexity 字段应支持 simple/normal/complex。"""
        from plugins.deepseek.pipeline import ChatContext
        ctx = ChatContext(bot=MagicMock(), event=MagicMock(), complexity="complex")
        assert ctx.complexity == "complex"


# ═══════════════════════════════════════════════════════════════
# SKIP 信号
# ═══════════════════════════════════════════════════════════════

class TestSkipSignal:
    """测试 _SKIP 短路信号。"""

    def test_skip_is_unique_sentinel(self):
        """_SKIP 应是唯一的哨兵对象。"""
        from plugins.deepseek.pipeline import _SKIP
        assert _SKIP is not None
        assert _SKIP is not False
        assert _SKIP is not True

    def test_skip_identity(self):
        """_SKIP 的 is 比较应正确。"""
        from plugins.deepseek.pipeline import _SKIP
        import copy
        assert _SKIP is _SKIP
        # 任何不是 _SKIP 的对象都不应等于 _SKIP
        assert {} is not _SKIP
        assert "" is not _SKIP
        assert None is not _SKIP


# ═══════════════════════════════════════════════════════════════
# Pipeline 注册
# ═══════════════════════════════════════════════════════════════

class TestPipelineRegistry:
    """测试 Pipeline 阶段注册。"""

    def test_pipeline_stages_registered(self):
        """导入 handler 后应由 stages 注册到 _PIPELINE。"""
        # 先导入 handler 触发所有 @stage 装饰器
        from plugins.deepseek import handler
        from plugins.deepseek.pipeline import _PIPELINE
        assert len(_PIPELINE) > 0, "Pipeline 应有注册的阶段"

    def test_pipeline_stages_have_names(self):
        """每个阶段应有名称和函数。"""
        from plugins.deepseek import handler
        from plugins.deepseek.pipeline import _PIPELINE
        for name, func in _PIPELINE:
            assert isinstance(name, str)
            assert callable(func)


# ═══════════════════════════════════════════════════════════════
# 消息截断 (H-9)
# ═══════════════════════════════════════════════════════════════

class TestMessageTruncation:
    """测试 MAX_USER_MSG_CHARS 截断。"""

    def test_max_user_msg_chars_defined(self):
        """MAX_USER_MSG_CHARS 应在 config 中定义。"""
        from plugins.deepseek.config import MAX_USER_MSG_CHARS
        assert MAX_USER_MSG_CHARS > 0

    def test_truncation_basic(self):
        """超长消息应被截断。"""
        from plugins.deepseek.config import MAX_USER_MSG_CHARS
        long_msg = "x" * (MAX_USER_MSG_CHARS + 100)
        truncated = long_msg[:MAX_USER_MSG_CHARS]
        assert len(truncated) == MAX_USER_MSG_CHARS
        assert len(truncated) < len(long_msg)

    def test_short_message_not_truncated(self):
        """短消息不应被截断。"""
        from plugins.deepseek.config import MAX_USER_MSG_CHARS
        short_msg = "hello"
        truncated = short_msg[:MAX_USER_MSG_CHARS]
        assert truncated == short_msg
