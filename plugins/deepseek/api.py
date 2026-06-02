"""DeepSeek API 调用层。
- 全局复用 aiohttp.ClientSession
- 带指数退避重试（3次）
- 用户级冷却限流
- 轻量级响应清洗（不过度过滤括号）
"""
import asyncio
import json
from typing import List, Dict, Any, Optional
import aiohttp

from .config import API_KEY, MODEL, BASE_URL, API_MAX_TOKENS
from .utils import clean_api_response

_http_session: Optional[aiohttp.ClientSession] = None

async def get_http_session() -> aiohttp.ClientSession:
    """获取全局复用的 HTTP Session。异常后自动重建。"""
    global _http_session
    if _http_session is None or _http_session.closed:
        _http_session = aiohttp.ClientSession()
    return _http_session

async def close_http_session():
    """关闭全局 HTTP Session。"""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None

async def call_deepseek_api(messages: List[Dict[str, str]], temperature: float = 0.9) -> str:
    if not API_KEY:
        return "API密钥没配置好喵..."

    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": API_MAX_TOKENS,
        "stream": False
    }

    last_exception = None

    for attempt in range(3):
        try:
            session = await get_http_session()
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status == 429:
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    return f"API出错啦...状态码{resp.status}"
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                return clean_api_response(content)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_exception = str(e)
            # 网络异常时重置 Session，下次自动重建
            global _http_session
            if _http_session:
                try:
                    await _http_session.close()
                except Exception:
                    pass
                _http_session = None
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            last_exception = str(e)
            await asyncio.sleep(2 ** attempt)

    return f"网络抽风了...{str(last_exception)[:50]}"
