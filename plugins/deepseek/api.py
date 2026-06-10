"""DeepSeek API 调用层。
- 全局复用 aiohttp.ClientSession
- 带指数退避重试（3次）
- 用户级冷却限流
- 轻量级响应清洗（不过度过滤括号）
- 本地 Ollama 离线降级（自动，对调用方透明）
"""
import asyncio
import json
import logging
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import aiohttp

from .config import API_KEY
from .config import API_MAX_TOKENS
from .config import BASE_URL
from .config import MODEL
from .utils import clean_api_response

logger = logging.getLogger("deepseek.api")

_http_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()


async def get_http_session() -> aiohttp.ClientSession:
    """获取全局复用的 HTTP Session。异常后自动重建。线程安全。"""
    global _http_session
    if _http_session is not None and not _http_session.closed:
        return _http_session
    async with _session_lock:
        # 双重检查：获取锁后再次确认
        if _http_session is None or _http_session.closed:
            _http_session = aiohttp.ClientSession()
        return _http_session


async def close_http_session():
    """关闭全局 HTTP Session。"""
    global _http_session
    if _http_session and not _http_session.closed:
        await _http_session.close()
        _http_session = None


async def _call_deepseek_raw(messages: List[Dict[str, str]], temperature: float = 0.9,
                             max_tokens: int = None) -> Optional[str]:
    """调用 DeepSeek 远程 API。成功返回内容，失败返回 None。"""
    if not API_KEY:
        logger.warning("[API] API密钥未配置")
        return None

    url = f"{BASE_URL}/chat/completions"
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens if max_tokens is not None else API_MAX_TOKENS,
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
                    logger.warning(f"[API] 状态码 {resp.status}，将降级到本地模型")
                    return None
                data = await resp.json()
                content = data["choices"][0]["message"]["content"]
                return clean_api_response(content)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_exception = str(e)
            global _http_session
            async with _session_lock:
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

    logger.warning(f"[API] 远程调用失败({last_exception})，将降级到本地模型")
    return None


async def _call_local_llm(messages: List[Dict[str, str]], temperature: float = 0.7) -> Optional[str]:
    """降级方案：调用本地 Ollama 模型。"""
    try:
        from .local_llm import call_ollama_chat
        from .local_llm import check_ollama_available
        if not await check_ollama_available():
            logger.warning("[API] 本地 Ollama 服务也不可用")
            return None
        result = await call_ollama_chat(messages, temperature=temperature)
        if result and "连接失败" not in result and "超时" not in result and "出错" not in result:
            return result
        return None
    except Exception as e:
        logger.warning(f"[API] 本地模型调用失败: {e}")
        return None


async def call_deepseek_api(messages: List[Dict[str, str]], temperature: float = 0.9,
                           task_type: str = "chat", max_tokens: int = None) -> str:
    """统一 API 入口 - 三层降级 + 任务分级路由（对调用方完全透明）。

    task_type 控制模型选择和 max_tokens：
    - "chat": 主聊天回复（默认，用配置模型，max_tokens=1500）
    - "analysis": 情感/上下文分析（短输出，max_tokens=300）
    - "extract": 标签/信息提取（短输出，max_tokens=500）
    - "summary": 摘要生成（中等输出，max_tokens=400）

    max_tokens 参数可覆盖 task_type 的默认值（功能⑤情绪驱动）。
    """
    # 任务分级：不同任务用不同的 max_tokens，节省成本
    if max_tokens is None:
        token_map = {
            "chat": API_MAX_TOKENS,
            "analysis": 300,
            "extract": 500,
            "summary": 400,
        }
        max_tokens = token_map.get(task_type, API_MAX_TOKENS)

    # ===== 第1层：DeepSeek 远程 API =====
    api_start = time.time()
    result = await _call_deepseek_raw(messages, temperature, max_tokens=max_tokens)
    api_duration = (time.time() - api_start) * 1000

    if result is not None:
        # 性能追踪
        try:
            from .performance_monitor import track_api_call
            tokens_est = len(result) // 2  # 粗略估算输出 token
            track_api_call(task_type, api_duration, tokens_used=tokens_est, success=True)
        except Exception:
            pass
        # Token 成本追踪
        try:
            from .token_tracker import get_tracker
            input_chars = sum(len(m.get("content", "")) for m in messages)
            get_tracker().record(
                task_type=task_type,
                model=MODEL,
                input_chars=input_chars,
                output_chars=len(result),
            )
        except Exception:
            pass
        return result

    # 远程失败，记录
    try:
        from .performance_monitor import track_api_call
        track_api_call(task_type, api_duration, success=False, error="remote_failed")
    except Exception:
        pass

    # ===== 第2层：本地 Ollama 模型 =====
    logger.info("[API] 降级到本地 Ollama 模型")
    local_result = await _call_local_llm(messages, temperature)
    if local_result is not None:
        return local_result

    # ===== 第3层：错误提示 =====
    return "唔…我脑子暂时转不过来了，稍后再聊好吗？"
