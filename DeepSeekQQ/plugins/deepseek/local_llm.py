"""本地 Ollama 模型降级方案（B19 实现）。

当 DeepSeek 远程 API 不可用时，自动降级到本地 Ollama 模型。
使用 aiohttp 直接调用 Ollama REST API（无需额外安装 ollama 包）。
"""
import asyncio
import logging
from typing import Dict
from typing import List
from typing import Optional

import aiohttp

logger = logging.getLogger("deepseek.local_llm")

# === 配置（可通过 .env 覆盖）—— 延迟加载避免 import 时 nonebot 未初始化 ===
def _load_ollama_config():
    """安全加载 Ollama 配置，测试环境 nonebot 未初始化时使用默认值。"""
    try:
        driver = __import__('nonebot', fromlist=['get_driver']).get_driver()
        cfg = driver.config
        base_url = str(getattr(cfg, 'ollama_base_url', 'http://localhost:11434') or 'http://localhost:11434').strip()
        model = str(getattr(cfg, 'ollama_model', 'qwen2.5:7b') or 'qwen2.5:7b').strip()
        return base_url, model
    except Exception:
        return 'http://localhost:11434', 'qwen2.5:7b'


_OLLAMA_BASE_URL, _OLLAMA_MODEL = _load_ollama_config()
OLLAMA_BASE_URL: str = _OLLAMA_BASE_URL
OLLAMA_MODEL: str = _OLLAMA_MODEL

OLLAMA_TIMEOUT: int = 60
OLLAMA_MAX_RETRIES: int = 1  # 本地 IPC 不需要重试；通过 OLLAMA_ENABLED 守卫可完全跳过


async def check_ollama_available() -> bool:
    """检查本地 Ollama 服务是否可用。

    Returns:
        True 如果 Ollama 服务运行且指定模型已拉取。
    """
    try:
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            # 检查服务是否运行
            async with session.get(f"{OLLAMA_BASE_URL}/api/tags") as resp:
                if resp.status != 200:
                    return False
                data = await resp.json()
                models = [m.get("name", "") for m in data.get("models", [])]
                # 检查目标模型是否可用（支持 :latest 后缀匹配）
                model_base = OLLAMA_MODEL.split(":")[0] if ":" in OLLAMA_MODEL else OLLAMA_MODEL
                available = any(
                    m == OLLAMA_MODEL or m.startswith(f"{model_base}:")
                    for m in models
                )
                if not available:
                    logger.warning(
                        f"[Ollama] 模型 {OLLAMA_MODEL} 未找到，可用模型: {models[:5]}"
                    )
                return available
    except aiohttp.ClientError:
        return False
    except Exception as e:
        logger.debug(f"[Ollama] 可用性检查失败: {e}")
        return False


async def call_ollama_chat(
    messages: List[Dict[str, str]],
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> Optional[str]:
    """调用本地 Ollama 模型进行对话。

    Args:
        messages: 消息列表 [{"role": "...", "content": "..."}]
        temperature: 温度参数 (0-2)
        max_tokens: 最大输出 token 数

    Returns:
        模型回复文本，失败返回 None。
    """
    # P0-4: Ollama 路径做 ChatML token 净化
    try:
        from .security import sanitize_for_ollama
        messages = sanitize_for_ollama(messages)
    except Exception:
        pass

    url = f"{OLLAMA_BASE_URL}/api/chat"
    payload = {
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "options": {
            "temperature": temperature,
        },
    }
    if max_tokens:
        payload["options"]["num_predict"] = max_tokens

    last_error = None
    for attempt in range(OLLAMA_MAX_RETRIES):
        try:
            timeout = aiohttp.ClientTimeout(total=OLLAMA_TIMEOUT)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(url, json=payload) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.warning(
                            f"[Ollama] HTTP {resp.status}: {error_text[:200]}"
                        )
                        last_error = f"HTTP {resp.status}"
                        await asyncio.sleep(2 ** attempt)
                        continue

                    data = await resp.json()
                    content = data.get("message", {}).get("content", "")
                    if content:
                        logger.info(
                            f"[Ollama] 成功调用 model={OLLAMA_MODEL} "
                            f"output_len={len(content)}"
                        )
                        return content.strip()
                    else:
                        logger.warning("[Ollama] 返回空内容")
                        return None

        except asyncio.TimeoutError:
            logger.debug(f"[Ollama] 超时 (attempt {attempt + 1})")
            last_error = "timeout"
            await asyncio.sleep(2 ** attempt)
        except aiohttp.ClientError as e:
            logger.debug(f"[Ollama] 连接错误: {e}")
            last_error = str(e)
            await asyncio.sleep(2 ** attempt)
        except Exception as e:
            logger.warning(f"[Ollama] 未知错误: {e}")
            return None

    logger.debug(f"[Ollama] 全部 {OLLAMA_MAX_RETRIES} 次重试失败: {last_error}")
    return None
