"""上下文压缩器 — 基于 WTFLLM 核心记忆设计。

当对话历史超过 CORE_MEMORY_TOKEN_LIMIT (512 tok) 时，
自动将前半段压缩为摘要，保持后半段原始内容。

策略：
- 压缩比约 0.2（压缩到原大小的 20%）
- 使用 LLM 调用进行语义压缩
- 熔断器：压缩失败时回退到简单截断
- 摘要缓存：同一次压缩后的摘要可复用多次
"""

import re
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from nonebot import logger

from .config import COMPRESS_TOKEN_THRESHOLD

# 核心记忆 token 上限
CORE_MEMORY_TOKEN_LIMIT = 512
# 压缩比例（压缩后约为原来的 20%）
COMPRESS_RATIO = 0.2
# 压缩失败计数器上限（熔断）
CIRCUIT_BREAKER_MAX_FAILURES = 3


from .token_utils import estimate_tokens


def estimate_messages_tokens(messages: List[Dict[str, str]]) -> int:
    """估算消息列表的总 token 数。"""
    total = 0
    for msg in messages:
        total += estimate_tokens(msg.get("content", ""))
        total += 4  # role 标记开销
    return total


class ContextCompressor:
    """上下文压缩器。

    维护每个 session 的压缩状态，包括失败计数器。
    """

    def __init__(self):
        self._failure_counts: Dict[str, int] = {}
        self._last_summaries: Dict[str, str] = {}  # session_id → 最近摘要
        self._last_message_counts: Dict[str, int] = {}  # 压缩时的消息数

    def needs_compression(self, messages: List[Dict[str, str]]) -> bool:
        """判断消息列表是否需要压缩。"""
        tokens = estimate_messages_tokens(messages)
        return tokens > CORE_MEMORY_TOKEN_LIMIT

    def get_split_point(self, messages: List[Dict[str, str]]) -> int:
        """找到压缩分割点（前半段需要压缩的索引）。

        保持后半段约 50% 的 token budget，即保留最近的 ~256 token。
        """
        target_tokens = CORE_MEMORY_TOKEN_LIMIT // 2
        running = 0
        for i in range(len(messages) - 1, -1, -1):
            running += estimate_tokens(messages[i].get("content", "")) + 4
            if running >= target_tokens:
                return i
        return len(messages) // 2  # 兜底：对半分

    def should_skip_compression(self, session_id: str) -> bool:
        """检查熔断器：连续失败超过阈值时跳过压缩。"""
        return self._failure_counts.get(session_id, 0) >= CIRCUIT_BREAKER_MAX_FAILURES

    def record_success(self, session_id: str):
        """记录压缩成功，重置失败计数。"""
        self._failure_counts[session_id] = 0

    def record_failure(self, session_id: str):
        """记录压缩失败。"""
        count = self._failure_counts.get(session_id, 0) + 1
        self._failure_counts[session_id] = count
        if count >= CIRCUIT_BREAKER_MAX_FAILURES:
            logger.warning(
                f"[压缩] 熔断触发 session={session_id[:20]}... "
                f"连续{count}次失败，后续将跳过压缩直到重启"
            )

    def cache_summary(self, session_id: str, summary: str, msg_count: int):
        """缓存压缩摘要（内存 + DB 持久化，B10+B22）。"""
        self._last_summaries[session_id] = summary
        self._last_message_counts[session_id] = msg_count
        # B10+B22: 异步持久化到 memory_summaries 表
        try:
            from .utils import safe_task
            safe_task(self._persist_summary(session_id, summary))
        except Exception as e:
            logger.warning(f"[压缩] 摘要持久化调度失败: {e}")

    async def _persist_summary(self, session_id: str, summary: str):
        """B10+B22: 将压缩摘要持久化到 DB，跨重启存活。"""
        try:
            from .db_session import append_memory_summary
            await append_memory_summary(session_id, summary)
        except Exception as e:
            logger.warning(f"[压缩] 摘要持久化写入失败: {e}")

    async def get_cached_summary_async(self, session_id: str, msg_count: int) -> Optional[str]:
        """获取缓存的摘要（内存优先，DB 兜底，B10+B22）。"""
        # 1. 先查内存缓存
        cached_count = self._last_message_counts.get(session_id, 0)
        if msg_count == cached_count:
            mem_result = self._last_summaries.get(session_id)
            if mem_result:
                return mem_result
        # 2. B10+B22: 内存未命中时查 DB（跨重启恢复）
        try:
            from .db_session import get_memory_summary
            db_summary = await get_memory_summary(session_id)
            if db_summary:
                # 同步到内存缓存
                self._last_summaries[session_id] = db_summary
                self._last_message_counts[session_id] = msg_count
                return db_summary
        except Exception:
            pass
        return None

    def get_cached_summary(self, session_id: str, msg_count: int) -> Optional[str]:
        """获取缓存的摘要（仅当消息数未增长时有效）。"""
        cached_count = self._last_message_counts.get(session_id, 0)
        if msg_count == cached_count:
            return self._last_summaries.get(session_id)
        return None


# 全局单例
compressor = ContextCompressor()


async def compress_context(
    session_id: str,
    messages: List[Dict[str, str]],
    api_call_fn=None,
) -> Tuple[List[Dict[str, str]], bool]:
    """压缩对话上下文。

    返回 (压缩后的消息列表, 是否实际压缩)。

    Args:
        session_id: 会话 ID
        messages: 原始消息列表 [{"role": ..., "content": ...}, ...]
        api_call_fn: LLM API 调用函数，若为 None 则使用默认 api.call_deepseek_api。
                     可注入 mock 用于测试。
    """
    if not compressor.needs_compression(messages):
        return messages, False

    if compressor.should_skip_compression(session_id):
        # 熔断：简单截断
        logger.debug(f"[压缩] 熔断中，简单截断 session={session_id[:20]}...")
        split = compressor.get_split_point(messages)
        return messages[split:], False

    msg_count = len(messages)
    # B10+B22: 内存缓存优先，未命中时查 DB
    cached = compressor.get_cached_summary(session_id, msg_count)
    if not cached:
        cached = await compressor.get_cached_summary_async(session_id, msg_count)
    if cached:
        # 使用缓存摘要
        split = compressor.get_split_point(messages)
        summary_msg = {"role": "system", "content": f"[之前的对话摘要] {cached}"}
        compressed = [summary_msg] + messages[split:]
        logger.debug(f"[压缩] 使用缓存摘要 session={session_id[:20]}...  {msg_count}→{len(compressed)}")
        return compressed, True

    try:
        split = compressor.get_split_point(messages)
        old_messages = messages[:split]
        recent_messages = messages[split:]

        # 构建压缩对话
        dialog_lines = []
        for m in old_messages:
            role_name = "用户" if m["role"] == "user" else "你"
            content = m["content"][:200]  # 每句最多200字
            dialog_lines.append(f"{role_name}：{content}")

        dialog_text = "\n".join(dialog_lines)
        target_words = max(30, int(len(dialog_text) * COMPRESS_RATIO))

        compress_prompt = (
            f"请用{target_words}字以内总结以下对话的核心内容，"
            f"保留关键信息（人物、事件、决定、情绪变化）。\n\n{dialog_text}"
        )

        compress_messages = [
            {"role": "system", "content": "你是一个对话压缩助手。只输出压缩后的摘要，不要其他内容。"},
            {"role": "user", "content": compress_prompt},
        ]

        if api_call_fn is None:
            from . import api
            api_call_fn = api.call_deepseek_api
        summary = await api_call_fn(
            compress_messages,
            temperature=0.3,
            task_type="compress",
        )
        summary = summary.strip()[:300]

        if summary:
            summary_msg = {
                "role": "system",
                "content": f"[之前的对话摘要] {summary}",
            }
            compressed = [summary_msg] + recent_messages
            compressor.record_success(session_id)
            compressor.cache_summary(session_id, summary, msg_count)
            logger.info(
                f"[压缩] session={session_id[:20]}... "
                f"{msg_count}→{len(compressed)} 条消息 "
                f"({estimate_messages_tokens(messages)}→{estimate_messages_tokens(compressed)} tok)"
            )
            return compressed, True
        else:
            # 压缩返回空，回退截断
            compressor.record_failure(session_id)
            return messages[split:], False

    except Exception as e:
        logger.warning(f"[压缩] 失败 session={session_id[:20]}...: {e}")
        compressor.record_failure(session_id)
        split = compressor.get_split_point(messages)
        return messages[split:], False


def get_compression_stats() -> Dict:
    """获取压缩统计信息。"""
    return {
        "failure_counts": dict(compressor._failure_counts),
        "cached_sessions": len(compressor._last_summaries),
        "core_token_limit": CORE_MEMORY_TOKEN_LIMIT,
    }
