"""DeepSeek API 调用层。
- 全局复用 aiohttp.ClientSession（健康检查 + 自动重建）
- 带指数退避重试（3次，仅对 429 和瞬时网络错误）
- 用户级冷却限流
- 轻量级响应清洗（不过度过滤括号）
- 降级：DeepSeek 远程 API → 友好错误提示
  （Ollama 本地降级待实现，见 _call_local_llm 注释）
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
                             max_tokens: int = None,
                             api_config: dict = None) -> Optional[str]:
    """调用 DeepSeek 远程 API。成功返回内容，失败返回 None。

    Phase 0.5: 可选 api_config dict 覆盖 API_KEY/MODEL/BASE_URL。
    未提供时使用 config.py 全局值（向后兼容）。

    P0-9: 集成 CircuitBreaker，熔断时直接跳过远程调用。
    """
    # Phase 0.5: 动态 Key 路由
    if api_config:
        _api_key = api_config.get("api_key", API_KEY)
        _base_url = api_config.get("base_url", BASE_URL)
        _model = api_config.get("model", MODEL)
    else:
        _api_key = API_KEY
        _base_url = BASE_URL
        _model = MODEL

    if not _api_key:
        logger.warning("[API] API密钥未配置")
        return None

    # P0-9: 检查熔断器状态
    from .circuit_breaker import get_breaker
    breaker = get_breaker("deepseek_api")
    if breaker and breaker._is_open() and not api_config:
        # 自带 Key 的使用者不触发全局熔断
        logger.warning("[API] DeepSeek API 已熔断，跳过远程调用")
        return None

    url = f"{_base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {_api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": _model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens if max_tokens is not None else API_MAX_TOKENS,
        "stream": False
    }

    last_exception = None

    for attempt in range(3):
        try:
            # BUGFIX: 每次重试都重新获取 session，防止并发调用关闭共享 session 导致活锁
            session = await get_http_session()
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60, connect=5, sock_read=30)
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
                # P0-9: 记录成功，重置熔断器
                if breaker:
                    breaker.fail_count = 0
                    breaker.state = "closed"
                return clean_api_response(content)
        except (asyncio.TimeoutError, aiohttp.ClientError) as e:
            last_exception = str(e)
            # 仅在连接级错误（非超时）时重建 session，避免影响并发请求
            is_fatal = isinstance(e, aiohttp.ClientConnectionError)
            if is_fatal:
                global _http_session
                async with _session_lock:
                    if _http_session:
                        try:
                            await _http_session.close()
                        except Exception as close_err:
                            logger.debug(f"[API] 关闭会话失败: {close_err}")
                        _http_session = None
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            last_exception = str(e)
            await asyncio.sleep(2 ** attempt)

    # P0-9: 记录失败到熔断器
    if breaker:
        breaker.fail_count += 1
        breaker.last_fail_time = time.time()
        if breaker.fail_count >= breaker.fail_threshold:
            breaker.state = "open"
            logger.warning(
                f"[熔断] deepseek_api 连续失败 {breaker.fail_count} 次，"
                f"熔断 {breaker.recovery_seconds}s"
            )

    logger.warning(f"[API] 远程调用失败({last_exception})，将降级到本地模型")
    return None


async def _call_local_llm(messages: List[Dict[str, str]], temperature: float = 0.7) -> Optional[str]:
    """降级方案：调用本地 Ollama 模型。

    P0-9: 使用缓存检查 Ollama 可用性（60s TTL）+ CircuitBreaker 保护。
    """
    # Ollama 未启用时直接返回 None，上游 call_deepseek_api 会返回友好错误提示
    from .config import OLLAMA_ENABLED
    if not OLLAMA_ENABLED:
        return None

    try:
        # P0-9: 使用缓存检查 Ollama 可用性
        from .circuit_breaker import is_ollama_available_cached
        if not await is_ollama_available_cached():
            logger.warning("[API] 本地 Ollama 服务不可用（缓存结果）")
            return None

        # P0-9: Ollama 熔断器检查
        from .circuit_breaker import get_breaker
        ollama_breaker = get_breaker("ollama_api")
        if ollama_breaker and ollama_breaker._is_open():
            logger.warning("[API] Ollama API 已熔断，跳过本地调用")
            return None

        from .local_llm import call_ollama_chat  # noqa: F811
        result = await call_ollama_chat(messages, temperature=temperature)

        if result and "连接失败" not in result and "超时" not in result and "出错" not in result:
            # P0-9: 记录 Ollama 成功
            if ollama_breaker:
                ollama_breaker.fail_count = 0
                ollama_breaker.state = "closed"
            return result

        # P0-9: 记录 Ollama 失败
        if ollama_breaker and result is None:
            ollama_breaker.fail_count += 1
            ollama_breaker.last_fail_time = time.time()
            if ollama_breaker.fail_count >= ollama_breaker.fail_threshold:
                ollama_breaker.state = "open"
        return None
    except Exception as e:
        logger.warning(f"[API] 本地模型调用失败: {e}")
        # P0-9: 记录异常到熔断器
        try:
            from .circuit_breaker import get_breaker
            b = get_breaker("ollama_api")
            if b:
                b.fail_count += 1
                b.last_fail_time = time.time()
                if b.fail_count >= b.fail_threshold:
                    b.state = "open"
        except Exception:
            pass
        return None


async def call_deepseek_api(messages: List[Dict[str, str]], temperature: float = 0.9,
                           task_type: str = "chat", max_tokens: int = None,
                           api_config: dict = None) -> str:
    """统一 API 入口 - 二层降级 + 任务分级路由（对调用方完全透明）。

    Phase 0.5: 可选 api_config dict（api_key/base_url/model），
    支持每 Bot 动态 Key 路由。未提供时用 config.py 全局值。

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
    result = await _call_deepseek_raw(messages, temperature, max_tokens=max_tokens, api_config=api_config)
    api_duration = (time.time() - api_start) * 1000

    if result is not None:
        # 性能追踪
        try:
            from .performance_monitor import track_api_call
            tokens_est = len(result) // 2  # 粗略估算输出 token
            track_api_call(task_type, api_duration, tokens_used=tokens_est, success=True)
        except Exception as e:
            logger.debug(f"[API] 性能追踪异常: {e}")
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
        except Exception as e:
            logger.debug(f"[API] Token追踪异常: {e}")
        return result

    # 远程失败，记录
    try:
        from .performance_monitor import track_api_call
        track_api_call(task_type, api_duration, success=False, error="remote_failed")
    except Exception as e:
        logger.debug(f"[API] 性能追踪异常: {e}")

    # ===== 第2层：本地 Ollama 模型 =====
    logger.info("[API] 降级到本地 Ollama 模型")
    local_result = await _call_local_llm(messages, temperature)
    if local_result is not None:
        return local_result

    # ===== 第3层：错误提示 =====
    return "唔…我脑子暂时转不过来了，稍后再聊好吗？"
