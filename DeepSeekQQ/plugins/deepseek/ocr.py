"""OCR 文字提取模块 - 基于 RapidOCR（PaddleOCR 的轻量替代）。
- 纯本地运行，完全离线
- 支持中英文混合识别
- 全局可调用：from .ocr import extract_text_from_image
- 异步版本：extract_text_from_image_async 通过 asyncio.to_thread 避免阻塞事件循环
"""
import io
from pathlib import Path
from typing import Optional

import requests
from nonebot import logger
from PIL import Image

_ocr_engine = None


def _get_engine():
    """懒加载 OCR 引擎（首次调用时初始化）。"""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _ocr_engine = RapidOCR()
            logger.info("[OCR] RapidOCR 引擎初始化完成")
        except ImportError:
            logger.error("[OCR] rapidocr-onnxruntime 未安装，请运行: pip install rapidocr-onnxruntime")
            return None
    return _ocr_engine


def extract_text_from_image(source: str, lang: str = "ch") -> str:
    """从图片中提取文字（同步版本，适合通过 asyncio.to_thread 调用避免阻塞）。

    Args:
        source: 图片文件路径 或 HTTP(S) URL
        lang: 语言，"ch" 中英混合，"en" 纯英文

    Returns:
        提取到的文字内容，失败时返回空字符串

    Note:
        本函数使用同步 requests 库。在异步上下文中请使用
        extract_text_from_image_async() 或 asyncio.to_thread(extract_text_from_image, source)。
    """
    engine = _get_engine()
    if engine is None:
        return ""

    # 获取图片
    try:
        if source.startswith(("http://", "https://")):
            resp = requests.get(source, timeout=15)
            resp.raise_for_status()
            img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        else:
            p = Path(source)
            if not p.exists():
                logger.warning(f"[OCR] 文件不存在: {source}")
                return ""
            img = Image.open(p).convert("RGB")
    except Exception as e:
        logger.warning(f"[OCR] 图片加载失败: {e}")
        return ""

    # OCR 识别
    return _recognize(engine, img)


def extract_text_from_bytes(img_bytes: bytes) -> str:
    """从图片字节数据中提取文字。"""
    engine = _get_engine()
    if engine is None:
        return ""
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    except Exception as e:
        logger.warning(f"[OCR] 图片加载失败: {e}")
        return ""
    return _recognize(engine, img)


async def extract_text_from_image_async(source: str, lang: str = "ch") -> str:
    """从图片中提取文字（异步版本，通过 to_thread 避免阻塞事件循环）。"""
    import asyncio
    return await asyncio.to_thread(extract_text_from_image, source, lang)


def _recognize(engine, img: Image.Image) -> str:
    """核心 OCR 识别逻辑。"""
    try:
        import numpy as np
        img_array = np.array(img)
        result, _ = engine(img_array)
        if not result:
            return ""
        # result 格式: [[box, text, confidence_str], ...]
        texts = [item[1] for item in result if float(item[2]) > 0.5]
        return "\n".join(texts)
    except Exception as e:
        logger.warning(f"[OCR] 识别失败: {e}")
        return ""
