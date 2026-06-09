"""本地 Ollama LLM 调用模块 - DeepSeek API 的离线降级方案。
- 纯本地运行，完全离线
- 支持文本生成和多轮对话
- 全局可调用：from .local_llm import call_ollama_chat
"""
import asyncio
import json
from typing import List, Dict, Optional

import aiohttp

from nonebot import logger

OLLAMA_HOST = "http://localhost:11434"
DEFAULT_MODEL = "moondream"  # 视觉模型，也支持基础文本


async def call_ollama_chat(
    messages: List[Dict[str, str]],
    model: str = DEFAULT_MODEL,
    host: str = OLLAMA_HOST,
    temperature: float = 0.7,
    timeout: int = 60,
) -> str:
    """调用本地 Ollama 进行文本对话。

    Args:
        messages: OpenAI 格式的消息列表 [{"role": "user", "content": "..."}]
        model: 模型名称
        host: Ollama 服务地址
        temperature: 温度参数
        timeout: 超时秒数

    Returns:
        模型回复文本，失败时返回错误信息
    """
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature},
    }

    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.post(
            f"{host}/api/chat",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    return f"本地模型异常，状态码: {resp.status}"
                data = await resp.json()
                return data.get("message", {}).get("content", "").strip() or "模型没有返回内容"
    except asyncio.TimeoutError:
        logger.warning("[LocalLLM] Ollama 响应超时")
        return "本地模型响应超时"
    except aiohttp.ClientError as e:
        logger.warning(f"[LocalLLM] Ollama 连接失败: {e}")
        return "本地模型连接失败"
    except Exception as e:
        logger.warning(f"[LocalLLM] 调用出错: {e}")
        return f"本地模型出错: {e}"



async def check_ollama_available(host: str = OLLAMA_HOST) -> bool:
    """检查 Ollama 服务是否可用。"""
    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.get(
            f"{host}/api/tags",
            timeout=aiohttp.ClientTimeout(total=5),
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


async def list_local_models(host: str = OLLAMA_HOST) -> List[str]:
    """列出本地已安装的模型。"""
    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.get(
            f"{host}/api/tags",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                return [m["name"] for m in data.get("models", [])]
    except Exception:
        return []
