"""图片视觉识别模块 - 五层降级方案（含智谱AI视觉层）。
- 第1层: 通义千问 VL API（远程视觉模型）→ 完整图片理解
- 第1.5层: 智谱AI GLM Vision（glm-4.6v）→ 中英文混合识别
- 第2层: Ollama 视觉模型（本地 moondream）→ 完整图片理解
- 第3层: OCR 文字提取（RapidOCR）→ 提取图中文字
- 第4层: 通用描述（告知LLM这是一张图片）
- 全局可调用：from .vision import analyze_image

analyze_image 返回 VisionResult 命名元组，包含:
  - description: 图片描述文本
  - source: 识别来源 ("qwen_vl" | "glm_vision" | "ollama" | "ocr" | "placeholder" | "error")
"""
import asyncio
import base64
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from typing import Tuple

import aiohttp

logger = logging.getLogger("deepseek.vision")

OLLAMA_HOST = "http://localhost:11434"
VISION_MODEL = "moondream"

# 图片下载缓存（LRU，防重复下载）
class _ImageCache(OrderedDict):
    MAX_SIZE = 200

    def __setitem__(self, key, value):
        if key in self:
            self.move_to_end(key)
        else:
            if len(self) >= self.MAX_SIZE:
                oldest = next(iter(self))
                del self[oldest]
        super().__setitem__(key, value)

_img_cache = _ImageCache()

# Ollama 可用性缓存（避免每次都尝试连接不存在的服务）
_ollama_available = True
_ollama_last_check = 0.0
_OLLAMA_CHECK_INTERVAL = 60  # 60秒内不重复检查


async def analyze_image(
    source: str,
    prompt: str = "请详细描述这张图片的内容",
) -> str:
    """分析图片，五层降级。

    Args:
        source: 图片文件路径 或 HTTP(S) URL
        prompt: 给视觉模型的提示词

    Returns:
        模型对图片的描述文字
    """
    # 获取图片 base64（带缓存）
    if source.startswith(("http://", "https://")):
        img_b64 = await _download_and_encode(source)
        if img_b64 is None:
            logger.warning(f"[Vision] 图片下载失败: {source[:80]}")
            return _wrap_vision_result(source, "", "error")
    else:
        img_b64 = _read_file_as_b64(source)
        if img_b64 is None:
            logger.warning(f"[Vision] 文件不存在或读取失败: {source}")
            return _wrap_vision_result(source, "", "error")

    # ===== 第1层：通义千问 VL API =====
    if img_b64:
        result = await _try_qwen_vl(img_b64, prompt)
        if result:
            logger.info(f"[Vision] ✅ Qwen-VL 识别成功 ({len(result)}字)")
            return _wrap_vision_result(source, result, "qwen_vl")
        logger.info("[Vision] Qwen-VL 失败，降级到 GLM Vision")

    # ===== 第1.5层：智谱AI GLM Vision =====
    if img_b64:
        result = await _try_glm_vision(img_b64, prompt)
        if result:
            logger.info(f"[Vision] ✅ GLM Vision 识别成功 ({len(result)}字)")
            return _wrap_vision_result(source, result, "glm_vision")
        logger.info("[Vision] GLM Vision 失败，降级到 Ollama")

    # ===== 第2层：Ollama 本地视觉模型 =====
    if img_b64:
        result = await _try_ollama_vision(img_b64, prompt)
        if result:
            logger.info(f"[Vision] ✅ Ollama 识别成功 ({len(result)}字)")
            return _wrap_vision_result(source, result, "ollama")
        logger.info("[Vision] Ollama 失败，降级到 OCR")

    # ===== 第3层：OCR 文字提取（通过 asyncio.to_thread 避免阻塞事件循环）=====
    ocr_text = await _fallback_ocr_async(source)
    if ocr_text:
        logger.info(f"[Vision] ✅ OCR 提取成功 ({len(ocr_text)}字)")
        return _wrap_vision_result(source, ocr_text, "ocr")
    logger.info("[Vision] OCR 无文字，使用通用描述")

    # ===== 第4层：通用描述（让LLM知道这是一张图片，而非完全无信息）=====
    return _wrap_vision_result(source, "", "placeholder")


async def _try_qwen_vl(img_b64: str, prompt: str) -> Optional[str]:
    """第1层：调用通义千问 VL API 识别图片。"""
    from .config import QWEN_VL_API_KEY
    from .config import QWEN_VL_MODEL

    if not QWEN_VL_API_KEY:
        logger.debug("[Vision] Qwen-VL: API Key 未配置")
        return None

    url = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {QWEN_VL_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": QWEN_VL_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 500,
    }

    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"[Vision] Qwen-VL 状态码 {resp.status}: {text[:100]}")
                    return None
                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return content if content else None
    except asyncio.TimeoutError:
        logger.warning("[Vision] Qwen-VL 超时 (30s)")
        return None
    except Exception as e:
        logger.warning(f"[Vision] Qwen-VL 异常: {e}")
        return None


async def _try_glm_vision(img_b64: str, prompt: str) -> Optional[str]:
    """第1.5层：调用智谱AI GLM Vision API 识别图片。"""
    from .config import GLM_API_KEY
    from .config import GLM_VISION_MODEL

    if not GLM_API_KEY:
        logger.debug("[Vision] GLM: API Key 未配置")
        return None

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {GLM_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": GLM_VISION_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"}},
                    {"type": "text", "text": prompt},
                ],
            }
        ],
        "max_tokens": 500,
    }

    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.post(
            url, json=payload, headers=headers,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"[Vision] GLM 状态码 {resp.status}: {text[:100]}")
                    return None
                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return content if content else None
    except asyncio.TimeoutError:
        logger.warning("[Vision] GLM Vision 超时 (30s)")
        return None
    except Exception as e:
        logger.warning(f"[Vision] GLM Vision 异常: {e}")
        return None


async def _try_ollama_vision(img_b64: str, prompt: str) -> Optional[str]:
    """第2层：调用本地 Ollama 视觉模型。"""
    global _ollama_available, _ollama_last_check
    import time

    # 快速检查：如果之前发现不可用，短时间内不再尝试
    now = time.time()
    if not _ollama_available and (now - _ollama_last_check) < _OLLAMA_CHECK_INTERVAL:
        logger.debug("[Vision] Ollama: 上次检查不可用，跳过")
        return None

    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
    }
    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.post(
            f"{OLLAMA_HOST}/api/generate",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=45),
        ) as resp:
                _ollama_available = True
                _ollama_last_check = now
                if resp.status != 200:
                    if resp.status == 404:
                        logger.warning(f"[Vision] Ollama: 模型 '{VISION_MODEL}' 不存在，请运行: ollama pull {VISION_MODEL}")
                        _ollama_available = False
                    else:
                        logger.warning(f"[Vision] Ollama 状态码 {resp.status}")
                    return None
                data = await resp.json()
                text = data.get("response", "").strip()
                return text if text else None
    except asyncio.TimeoutError:
        logger.warning("[Vision] Ollama 超时 (45s)，模型可能在CPU上运行较慢")
        _ollama_available = False
        _ollama_last_check = now
        return None
    except aiohttp.ClientConnectorError:
        logger.warning(f"[Vision] Ollama: 无法连接到 {OLLAMA_HOST}，服务可能未启动")
        _ollama_available = False
        _ollama_last_check = now
        return None
    except Exception as e:
        logger.warning(f"[Vision] Ollama 异常: {type(e).__name__}: {e}")
        _ollama_available = False
        _ollama_last_check = now
        return None


def _wrap_vision_result(source: str, description: str, source_type: str) -> str:
    """将识别结果包装为统一格式字符串。

    格式: "[图片内容: {description}]" + 可选的来源标记。
    外部模块使用 extract_vision_text() 安全提取纯描述文本。
    """
    if source_type == "placeholder":
        return _PLACEHOLDER
    if source_type == "error":
        if source.startswith(("http://", "https://")):
            return _PLACEHOLDER
        return "[图片文件不存在]"
    if source_type == "ocr":
        return f"[图片中的文字内容]: {description}"
    return f"[图片内容: {description}]"


# 第4层占位：提供通用描述让LLM感知到图片存在并自然回应
_PLACEHOLDER = "[图片内容: 一张图片，具体内容未能识别]"
_ERROR_FILE_MISSING = "[图片文件不存在]"


def extract_vision_text(result: str) -> str:
    """从 analyze_image 的返回结果中安全提取纯描述文本。

    供外部模块（如 share_parser、image_reply）使用，
    避免直接字符串操作 `replace("[图片内容: ", "").replace("]", "")`。
    """
    if not result:
        return ""
    # 占位/错误检查必须在最前面（避免被后续前缀匹配误捕获）
    if result in (_PLACEHOLDER, _ERROR_FILE_MISSING):
        return ""
    # OCR 结果
    if result.startswith("[图片中的文字内容]: "):
        return result[len("[图片中的文字内容]: "):]
    # 正常描述
    if result.startswith("[图片内容: ") and result.endswith("]"):
        return result[len("[图片内容: "):-1]
    return result


def _fallback_ocr(source: str) -> str:
    """第3层：用 OCR 提取图片中的文字（同步版本，仅供非异步场景使用）。"""
    try:
        from .ocr import extract_text_from_image
        text = extract_text_from_image(source)
        return text
    except Exception as e:
        logger.warning(f"[Vision] OCR 降级也失败: {e}")
        return ""


async def _fallback_ocr_async(source: str) -> str:
    """第3层：用 OCR 提取图片中的文字（异步版本，通过 to_thread 避免阻塞事件循环）。"""
    return await asyncio.to_thread(_fallback_ocr, source)


def _read_file_as_b64(path: str) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return base64.b64encode(p.read_bytes()).decode("utf-8")
    except Exception:
        return None


async def _download_and_encode(url: str) -> Optional[str]:
    """下载图片并编码为 base64（带 LRU 缓存，避免重复下载）。"""
    cached = _img_cache.get(url)
    if cached is not None:
        return cached

    try:
        from .api import get_http_session
        session = await get_http_session()
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=20)
        ) as resp:
                if resp.status != 200:
                    logger.warning(f"[Vision] 图片下载失败 status={resp.status}: {url[:80]}")
                    return None
                data = await resp.read()
                if len(data) < 100:
                    logger.warning(f"[Vision] 图片下载数据异常小 ({len(data)} bytes): {url[:80]}")
                    return None
                b64 = base64.b64encode(data).decode("utf-8")
                _img_cache[url] = b64
                return b64
    except asyncio.TimeoutError:
        logger.warning(f"[Vision] 图片下载超时 (20s): {url[:80]}")
        return None
    except Exception as e:
        logger.warning(f"[Vision] 图片下载异常: {type(e).__name__}: {url[:80]}")
        return None


async def recognize_sticker(source: str) -> Optional[str]:
    """识别表情包的情绪，返回情绪关键词或 None。"""
    result = await analyze_image(source, "这个表情包表达什么情绪？只回答一个英文单词，如 happy/sad/angry/shy/cute/funny/love/tsundere")
    desc = extract_vision_text(result)
    if desc:
        emotion = desc.strip().lower().split()[0] if desc.strip() else None
        valid = {"happy", "sad", "angry", "shy", "cute", "funny", "love", "tsundere", "excited", "speechless", "surprised", "smug"}
        return emotion if emotion in valid else None
    return None
