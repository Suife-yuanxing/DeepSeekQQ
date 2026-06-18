"""工作摘要生成 — LLM 调用（opt-in + 消息截断）"""

import os
from typing import Any

import httpx


async def generate_summary(
    user_messages: list[dict[str, Any]],
    timeout: int = 10,
    max_retries: int = 2,
) -> str | None:
    """调用 DeepSeek API 生成工作摘要

    user_messages: [{"content": str, "timestamp": str, "session_id": str}, ...]

    返回摘要文本或 None（失败时）
    """
    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    if not api_key:
        return None

    # 消息截断：每条 ≤500 字符，总计 ≤8000 字符
    MAX_PER_MSG = 500
    MAX_TOTAL = 8000

    truncated: list[str] = []
    total_chars = 0
    for msg in user_messages:
        content = msg.get("content", "")
        if not content:
            continue
        snippet = content[:MAX_PER_MSG]
        if total_chars + len(snippet) > MAX_TOTAL:
            snippet = snippet[:MAX_TOTAL - total_chars]
            truncated.append(snippet)
            break
        truncated.append(snippet)
        total_chars += len(snippet)

    if not truncated:
        return None

    joined = "\n---\n".join(truncated)

    prompt = f"""以下是最近 {len(truncated)} 个 AI 编程会话的用户提问摘要。请用 3-5 句话总结主要工作内容：

{joined}"""

    for attempt in range(1 + max_retries):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    "https://api.deepseek.com/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "deepseek-chat",
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 200,
                        "temperature": 0.3,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    choices = data.get("choices", [])
                    if choices:
                        return choices[0]["message"]["content"].strip()
        except (httpx.TimeoutException, httpx.ConnectError, OSError):
            pass

        if attempt < max_retries:
            import asyncio
            await asyncio.sleep(2**attempt)

    return None


# 摘要缓存
_summary_cache: dict[str, str] = {}


def get_cached_summary(session_id: str) -> str | None:
    return _summary_cache.get(session_id)


def set_cached_summary(session_id: str, summary: str) -> None:
    _summary_cache[session_id] = summary
