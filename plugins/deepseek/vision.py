"""图片视觉识别模块 - 四层降级方案。
- 第1层: 通义千问 VL API（远程视觉模型）→ 完整图片理解
- 第2层: Ollama 视觉模型（本地 moondream）→ 完整图片理解
- 第3层: OCR 文字提取（RapidOCR）→ 提取图中文字
- 第4层: 返回占位信息
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
) -> str:
    """分析图片，四层降级。

    Args:
        source: 图片文件路径 或 HTTP(S) URL
        prompt: 给视觉模型的提示词

    Returns:
        模型对图片的描述文字
    """
    # 获取图片 base64
    if source.startswith(("http://", "https://")):
        img_b64 = await _download_and_encode(source)
        if img_b64 is None:
            return _fallback_ocr(source)
    else:
        img_b64 = _read_file_as_b64(source)
        if img_b64 is None:
            return "[图片文件不存在]"

    # ===== 第1层：通义千问 VL API =====
    if img_b64:
        result = await _try_qwen_vl(img_b64, prompt)
        if result:
            return result

    # ===== 第2层：Ollama 本地视觉模型 =====
    if img_b64:
        result = await _try_ollama_vision(img_b64, prompt)
        if result:
            return result

    # ===== 第3层：OCR 文字提取 =====
    ocr_text = _fallback_ocr(source)
    if ocr_text:
        return f"[图片中的文字内容]: {ocr_text}"

    # ===== 第4层：占位信息 =====
    return "[图片内容暂无法识别]"


async def _try_qwen_vl(img_b64: str, prompt: str) -> Optional[str]:
    """第1层：调用通义千问 VL API 识别图片。"""
    from .config import QWEN_VL_API_KEY, QWEN_VL_MODEL

    if not QWEN_VL_API_KEY:
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
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url, json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.warning(f"[Vision] Qwen-VL 状态码: {resp.status} {text[:100]}")
                    return None
                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                return content if content else None
    except asyncio.TimeoutError:
        logger.warning("[Vision] Qwen-VL 超时，降级到 Ollama")
        return None
    except Exception as e:
        logger.warning(f"[Vision] Qwen-VL 出错: {e}，降级到 Ollama")
        return None


async def _try_ollama_vision(img_b64: str, prompt: str) -> Optional[str]:
    """第2层：调用本地 Ollama 视觉模型。"""
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{OLLAMA_HOST}/api/generate",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                text = data.get("response", "").strip()
                return text if text else None
    except Exception:
        return None


def _fallback_ocr(source: str) -> str:
    """第3层：用 OCR 提取图片中的文字。"""
    try:
        from .ocr import extract_text_from_image
        text = extract_text_from_image(source)
        return text
    except Exception as e:
        logger.warning(f"[Vision] OCR 降级也失败: {e}")
        return ""


def _read_file_as_b64(path: str) -> Optional[str]:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return base64.b64encode(p.read_bytes()).decode("utf-8")
    except Exception:
        return None


async def _download_and_encode(url: str) -> Optional[str]:
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


async def recognize_sticker(source: str) -> Optional[str]:
    """识别表情包的情绪，返回情绪关键词或 None。"""
    result = await analyze_image(source, "这个表情包表达什么情绪？只回答一个英文单词，如 happy/sad/angry/shy/cute/funny/love/tsundere")
    if result and result != "[图片内容暂无法识别]" and result != "[图片文件不存在]" and "文字内容" not in result:
        emotion = result.strip().lower().split()[0] if result.strip() else None
        valid = {"happy", "sad", "angry", "shy", "cute", "funny", "love", "tsundere", "excited", "speechless", "surprised", "smug"}
        return emotion if emotion in valid else None
    return None
