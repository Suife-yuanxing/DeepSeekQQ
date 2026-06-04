"""图片视觉识别模块 - 三层降级方案。
- 第1层: Ollama 视觉模型（moondream）→ 完整图片理解
- 第2层: OCR 文字提取（RapidOCR）→ 提取图中文字
- 第3层: 返回占位信息
- 全局可调用：from .vision import analyze_image
"""
import base64
import asyncio
import logging
from pathlib import Path
from typing import Optional

import aiohttp

logger = logging.getLogger("deepseek.vision")

OLLAMA_HOST = "http://localhost:11434"
VISION_MODEL = "moondream"


async def analyze_image(
    source: str,
    prompt: str = "请详细描述这张图片的内容",
    model: str = VISION_MODEL,
    host: str = OLLAMA_HOST,
) -> str:
    """分析图片，三层降级：视觉模型 → OCR → 占位信息。

    Args:
        source: 图片文件路径 或 HTTP(S) URL
        prompt: 给视觉模型的提示词
        model: Ollama 视觉模型名称
        host: Ollama 服务地址

    Returns:
        模型对图片的描述文字
    """
    # 获取图片 base64
    if source.startswith(("http://", "https://")):
        img_b64 = await _download_and_encode(source)
        if img_b64 is None:
            # URL 下载失败，尝试直接用 OCR
            return _fallback_ocr(source)
    else:
        img_b64 = _read_file_as_b64(source)
        if img_b64 is None:
            return "[图片文件不存在]"

    # ===== 第1层：Ollama 视觉模型 =====
    if img_b64:
        result = await _try_vision_model(img_b64, prompt, model, host)
        if result:
            return result

    # ===== 第2层：OCR 文字提取 =====
    ocr_text = _fallback_ocr(source)
    if ocr_text:
        return f"[图片中的文字内容]: {ocr_text}"

    # ===== 第3层：占位信息 =====
    return "[图片内容暂无法识别]"


async def _try_vision_model(
    img_b64: str, prompt: str, model: str, host: str
) -> Optional[str]:
    """尝试用 Ollama 视觉模型分析图片。"""
    payload = {
        "model": model,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{host}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[Vision] Ollama 状态码: {resp.status}")
                    return None
                data = await resp.json()
                text = data.get("response", "").strip()
                return text if text else None
    except asyncio.TimeoutError:
        logger.warning("[Vision] Ollama 响应超时，降级到 OCR")
        return None
    except aiohttp.ClientError as e:
        logger.warning(f"[Vision] Ollama 连接失败: {e}，降级到 OCR")
        return None
    except Exception as e:
        logger.warning(f"[Vision] 调用出错: {e}，降级到 OCR")
        return None


def _fallback_ocr(source: str) -> str:
    """降级方案：用 OCR 提取图片中的文字。"""
    try:
        from .ocr import extract_text_from_image
        text = extract_text_from_image(source)
        return text
    except Exception as e:
        logger.warning(f"[Vision] OCR 降级也失败: {e}")
        return ""


def _read_file_as_b64(path: str) -> Optional[str]:
    """读取本地图片文件并返回 base64 编码。"""
    p = Path(path)
    if not p.exists():
        return None
    try:
        return base64.b64encode(p.read_bytes()).decode("utf-8")
    except Exception:
        return None


async def _download_and_encode(url: str) -> Optional[str]:
    """下载远程图片并返回 base64 编码。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    return None
                return base64.b64encode(await resp.read()).decode("utf-8")
    except Exception:
        return None
