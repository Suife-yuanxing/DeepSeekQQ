"""图片生成功能（功能④）。

使用 SiliconFlow API 生成图片。
用户提到特定场景时，概率性生成图片回复。

触发条件：
| 触发词           | 场景       | 概率 |
|-----------------|-----------|------|
| 自拍/照片/长什么样 | 猫娘自拍   | 15%  |
| 吃饭/美食/饿了    | 猫娘吃饭   | 10%  |
| 画/画一个/帮我画   | 主动绘画   | 80%  |
| 睡觉/晚安/困了    | 猫娘睡觉   | 10%  |
| 生日/蛋糕/庆祝    | 庆祝场景   | 20%  |
"""
import os
import re
import random
import hashlib
import asyncio
import aiohttp
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime

from nonebot import logger

from .config import IMAGE_CACHE_DIR, IMAGE_GEN_API_KEY, IMAGE_GEN_MODEL, IMAGE_GEN_BASE_URL

# 图片缓存目录
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

# 触发词配置（三类场景）
_IMAGE_TRIGGERS = {
    # 直接请求：用户明确要求生成图片（80%）
    "draw": {
        "keywords": ["画", "画一个", "帮我画", "画个", "画张", "生成图片", "生成一张", "出图", "画一幅"],
        "prob": 0.80,
        "prompt": "",  # 从用户消息提取
        "scene": "draw",
    },
    # Bot 自拍场景：用户想看 bot 的样子（30%）
    "selfie": {
        "keywords": ["自拍", "照片", "看看你", "你的样子", "长什么样", "发一张", "来一张"],
        "prob": 0.30,
        "prompt": "anime catgirl taking a selfie with phone, cute expression, cat ears, pink hair, QQ chat style",
        "scene": "selfie",
    },
    # 生活场景：用户描述场景，bot 配图（25%）
    "eating": {
        "keywords": ["吃饭", "美食", "饿了", "吃东西", "干饭", "午饭", "晚饭", "早饭", "做饭", "好吃的"],
        "prob": 0.25,
        "prompt": "anime catgirl eating delicious food happily, cat ears, cute table setting, warm lighting",
        "scene": "eating",
    },
    "sleep": {
        "keywords": ["睡觉", "晚安", "困了", "要睡了", "睡了", "好困"],
        "prob": 0.25,
        "prompt": "anime catgirl sleeping peacefully in bed, cat ears, soft blanket, moonlight, cozy bedroom",
        "scene": "sleep",
    },
    "celebrate": {
        "keywords": ["生日", "蛋糕", "庆祝", "节日", "快乐", "纪念"],
        "prob": 0.25,
        "prompt": "anime catgirl celebrating with cake and confetti, happy expression, cat ears, party decorations",
        "scene": "celebrate",
    },
}


def should_generate_image(user_msg: str) -> Optional[Dict[str, Any]]:
    """判断是否触发图片生成。

    Returns:
        触发配置 dict 或 None
    """
    for trigger_id, config in _IMAGE_TRIGGERS.items():
        for kw in config["keywords"]:
            if kw in user_msg:
                if random.random() < config["prob"]:
                    logger.info(f"[图片] 触发条件: {trigger_id} (keyword={kw})")
                    return {"id": trigger_id, **config}
    return None


def _extract_draw_prompt(user_msg: str) -> str:
    """从用户消息中提取绘画描述。"""
    cleaned = user_msg
    for kw in ["帮我画", "画一个", "画个", "画张", "画"]:
        cleaned = cleaned.replace(kw, "").strip()
    cleaned = re.sub(r'^[，。！？,\s]+|[，。！？,\s]+$', '', cleaned)

    if len(cleaned) < 2:
        return "anime catgirl in a cute pose, cat ears, kawaii style"
    return f"{cleaned}, anime style, high quality, detailed, cute"


async def generate_image(prompt: str) -> Optional[str]:
    """调用 SiliconFlow API 生成图片，返回本地缓存路径。

    API: POST {base_url}/images/generations
    """
    if not IMAGE_GEN_API_KEY:
        logger.warning("[图片] 未配置 IMAGE_GEN_API_KEY，跳过生成")
        return None

    # 缓存文件名
    prompt_hash = hashlib.md5(prompt.encode()).hexdigest()[:12]
    timestamp = datetime.now().strftime("%H%M%S")
    filename = f"img_{timestamp}_{prompt_hash}.jpg"
    cache_path = os.path.join(IMAGE_CACHE_DIR, filename)

    if os.path.exists(cache_path):
        return cache_path

    url = f"{IMAGE_GEN_BASE_URL.rstrip('/')}/images/generations"
    headers = {
        "Authorization": f"Bearer {IMAGE_GEN_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": IMAGE_GEN_MODEL,
        "prompt": prompt,
        "image_size": "512x512",
        "batch_size": 1,
        "num_inference_steps": 20,
    }

    try:
        logger.info(f"[图片] 正在生成: {prompt[:50]}...")
        timeout = aiohttp.ClientTimeout(total=60)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.error(f"[图片] API 错误 {resp.status}: {error_text[:200]}")
                    return None

                data = await resp.json()
                # SiliconFlow 响应格式: {"data": [{"url": "..."}]}
                images = data.get("data", [])
                if not images:
                    logger.error(f"[图片] 响应中无图片数据: {str(data)[:200]}")
                    return None

                image_url = images[0].get("url", "")
                if not image_url:
                    logger.error(f"[图片] 响应中无 URL: {str(data)[:200]}")
                    return None

                # 下载图片
                async with session.get(image_url) as img_resp:
                    if img_resp.status != 200:
                        logger.error(f"[图片] 下载失败: {img_resp.status}")
                        return None
                    img_data = await img_resp.read()
                    if len(img_data) < 1000:
                        logger.warning(f"[图片] 下载数据太小: {len(img_data)} bytes")
                        return None

                    with open(cache_path, "wb") as f:
                        f.write(img_data)
                    logger.info(f"[图片] 生成成功: {filename} ({len(img_data)} bytes)")
                    return cache_path

    except asyncio.TimeoutError:
        logger.warning("[图片] 生成超时 (60s)")
        return None
    except Exception as e:
        logger.error(f"[图片] 生成失败: {e}")
        return None


async def cleanup_old_images(max_age_hours: int = 24):
    """清理旧的图片缓存。"""
    try:
        now = datetime.now().timestamp()
        cutoff = now - max_age_hours * 3600
        count = 0
        for f in Path(IMAGE_CACHE_DIR).glob("img_*.jpg"):
            if f.stat().st_mtime < cutoff:
                f.unlink()
                count += 1
        if count > 0:
            logger.info(f"[图片] 清理了 {count} 张过期图片")
    except Exception as e:
        logger.warning(f"[图片] 清理失败: {e}")
