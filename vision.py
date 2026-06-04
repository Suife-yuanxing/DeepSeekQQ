#!/usr/bin/env python3
"""图片识别工具 - 三层降级方案（独立运行，无需 nonebot）。

用法:
    python vision.py <图片路径或URL>
    python vision.py <图片路径或URL> "自定义提示词"

三层降级:
    1. Ollama 视觉模型 (moondream) → 完整图片理解
    2. OCR 文字提取 (RapidOCR) → 提取图中文字
    3. 返回占位信息

示例:
    python vision.py photo.jpg
    python vision.py https://example.com/img.png "这张图里有什么动物?"
    python vision.py screenshot.png "识别图中的文字"
"""
import base64
import io
import sys
from pathlib import Path

import requests

OLLAMA_HOST = "http://localhost:11434"
VISION_MODEL = "moondream"


def analyze_image(source: str, prompt: str = "请用中文详细描述这张图片的内容") -> str:
    """分析图片，三层降级。"""
    # 获取图片
    if source.startswith(("http://", "https://")):
        try:
            resp = requests.get(source, timeout=15)
            resp.raise_for_status()
            img_bytes = resp.content
            img_b64 = base64.b64encode(img_bytes).decode("utf-8")
        except Exception as e:
            return f"图片下载失败: {e}"
    else:
        p = Path(source)
        if not p.exists():
            return f"图片文件不存在: {source}"
        img_bytes = p.read_bytes()
        img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    # ===== 第1层：Ollama 视觉模型 =====
    result = _try_vision(img_b64, prompt)
    if result:
        return result

    # ===== 第2层：OCR 文字提取 =====
    ocr_text = _try_ocr(img_bytes)
    if ocr_text:
        return f"[图片中的文字内容]:\n{ocr_text}"

    # ===== 第3层：占位信息 =====
    return "[图片内容暂无法识别]"


def _try_vision(img_b64: str, prompt: str) -> str:
    """尝试 Ollama 视觉模型。"""
    payload = {
        "model": VISION_MODEL,
        "prompt": prompt,
        "images": [img_b64],
        "stream": False,
    }
    try:
        resp = requests.post(f"{OLLAMA_HOST}/api/generate", json=payload, timeout=30)
        if resp.status_code != 200:
            print(f"  [视觉模型] 状态码: {resp.status_code}")
            return ""
        text = resp.json().get("response", "").strip()
        return text
    except requests.Timeout:
        print("  [视觉模型] 超时，降级到 OCR")
        return ""
    except requests.ConnectionError:
        print("  [视觉模型] Ollama 连接失败，降级到 OCR")
        return ""
    except Exception as e:
        print(f"  [视觉模型] 出错: {e}，降级到 OCR")
        return ""


def _try_ocr(img_bytes: bytes) -> str:
    """尝试 OCR 文字提取。"""
    try:
        from rapidocr_onnxruntime import RapidOCR
        from PIL import Image
        import numpy as np

        engine = RapidOCR()
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        img_array = np.array(img)
        result, _ = engine(img_array)
        if not result:
            return ""
        texts = [item[1] for item in result if float(item[2]) > 0.5]
        return "\n".join(texts)
    except ImportError:
        print("  [OCR] rapidocr-onnxruntime 未安装，跳过")
        return ""
    except Exception as e:
        print(f"  [OCR] 出错: {e}")
        return ""


def main():
    if len(sys.argv) < 2:
        print("用法: python vision.py <图片路径或URL> [提示词]")
        print("示例: python vision.py photo.jpg")
        print('      python vision.py https://example.com/img.png "描述图片"')
        sys.exit(1)

    source = sys.argv[1]
    prompt = sys.argv[2] if len(sys.argv) > 2 else "请用中文详细描述这张图片的内容"

    print(f"分析图片: {source[:80]}")
    print(f"提示词: {prompt}")
    print("-" * 50)
    result = analyze_image(source, prompt)
    print(result)


if __name__ == "__main__":
    main()
