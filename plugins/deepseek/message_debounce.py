"""消息防抖模块。

4秒窗口内同一会话的多条消息合并为一条处理，避免连续消息导致重复回复。
"""

import asyncio
from dataclasses import dataclass
from dataclasses import field
from typing import Awaitable
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot
from nonebot.adapters.onebot.v11 import MessageEvent


@dataclass
class PendingSession:
    """待处理的会话消息队列。"""
    messages: List[MessageEvent] = field(default_factory=list)
    timer: Optional[asyncio.Task] = None
    first_message_time: float = 0.0


class MessageDebouncer:
    """消息防抖器。

    工作原理：
    1. 收到消息时，加入对应 session 的队列
    2. 重置 4 秒定时器
    3. 定时器到期后，合并所有消息为一条，调用 handler 处理
    4. 同一会话（user_id + group_id 组合）共享队列
    """

    def __init__(self, delay_seconds: float = 4.0):
        """初始化防抖器。

        Args:
            delay_seconds: 防抖窗口秒数，默认 4 秒
        """
        self._delay = delay_seconds
        self._sessions: Dict[str, PendingSession] = {}
        self._lock = asyncio.Lock()

    def _get_session_key(self, event: MessageEvent) -> str:
        """生成会话唯一键。

        私聊: user_<user_id>
        群聊: group_<group_id>_<user_id>
        """
        user_id = str(event.user_id)
        group_id = getattr(event, 'group_id', None)
        if group_id:
            return f"group_{group_id}_{user_id}"
        return f"user_{user_id}"

    async def add_message(
        self,
        bot: Bot,
        event: MessageEvent,
        handler: Callable[[Bot, MessageEvent], Awaitable[None]]
    ) -> None:
        """添加消息到防抖队列。

        Args:
            bot: Bot 实例
            event: 消息事件
            handler: 实际处理函数（handle_chat）
        """
        session_key = self._get_session_key(event)
        msg_text = str(event.get_message()).strip()[:30]  # 截取前30字用于日志

        async with self._lock:
            if session_key not in self._sessions:
                self._sessions[session_key] = PendingSession()

            session = self._sessions[session_key]
            session.messages.append(event)
            msg_count = len(session.messages)

            # 取消旧定时器
            if session.timer and not session.timer.done():
                session.timer.cancel()
                logger.debug(f"[Debounce] 重置定时器 session={session_key}")

            # 记录第一条消息时间
            if msg_count == 1:
                session.first_message_time = asyncio.get_event_loop().time()

            logger.info(f"[Debounce] 入队 session={session_key} 累计{msg_count}条: {msg_text}")

            # 启动新定时器
            session.timer = asyncio.create_task(
                self._wait_and_process(session_key, bot, handler)
            )

    async def _wait_and_process(
        self,
        session_key: str,
        bot: Bot,
        handler: Callable[[Bot, MessageEvent], Awaitable[None]]
    ) -> None:
        """等待防抖窗口到期，然后处理合并的消息。"""
        try:
            await asyncio.sleep(self._delay)
        except asyncio.CancelledError:
            return  # 被新消息取消，不处理

        async with self._lock:
            session = self._sessions.pop(session_key, None)
            if not session or not session.messages:
                return

            messages = session.messages

        # 合并消息处理
        try:
            if len(messages) == 1:
                # 单条消息，直接处理
                logger.info(f"[Debounce] 单条消息直接处理 session={session_key}")
                await handler(bot, messages[0])
            else:
                # 多条消息，合并为一条
                logger.info(f"[Debounce] 合并{len(messages)}条消息处理 session={session_key}")
                merged_event = self._merge_messages(messages)
                await handler(bot, merged_event)
        except Exception as e:
            # 记录错误但不中断
            logger.exception(f"[Debounce] 处理合并消息出错: {e}")

    def _merge_messages(self, messages: List[MessageEvent]) -> MessageEvent:
        """合并多条消息为一条。

        策略：
        - 取最后一条消息作为基础（保留最新的事件信息）
        - 将所有消息文本用换行连接
        - 保留第一条消息的非文本段（图片/表情/语音等）
        """
        if not messages:
            raise ValueError("无法合并空消息列表")

        from nonebot.adapters.onebot.v11 import Message
        from nonebot.adapters.onebot.v11 import MessageSegment

        # 取最后一条作为基础
        merged = messages[-1]

        # 分离文本和非文本段
        text_parts = []
        non_text_segments = []

        for msg in messages:
            for seg in msg.get_message():
                if seg.type == "text":
                    text = str(seg).strip()
                    if text:
                        text_parts.append(text)
                elif not non_text_segments:
                    # 只保留第一条消息的非文本内容（图片/表情/语音等）
                    non_text_segments.append(seg)

        # 构建合并后的消息：非文本段 + 合并文本
        parts = non_text_segments[:]
        if text_parts:
            parts.append(MessageSegment.text("\n".join(text_parts)))

        if parts:
            # OneBot V11 的 get_message() 返回 self.message 字段，
            # 直接设置 message 属性（pydantic field）而非私有 _message
            merged.message = Message(parts)

        # 记录合并信息（用于日志）
        merged._debounce_merged_count = len(messages)

        return merged

    def get_pending_count(self, session_key: str) -> int:
        """获取指定会话待处理消息数。"""
        session = self._sessions.get(session_key)
        return len(session.messages) if session else 0

    def clear_session(self, session_key: str) -> None:
        """清除指定会话的待处理消息。"""
        session = self._sessions.pop(session_key, None)
        if session and session.timer and not session.timer.done():
            session.timer.cancel()


# 全局单例
debouncer = MessageDebouncer(delay_seconds=4.0)
