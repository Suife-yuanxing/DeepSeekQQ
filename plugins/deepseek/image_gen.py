"""图片生成功能。

使用 SiliconFlow/Agnes API 生成图片。
用户提到特定场景时，概率性生成图片回复。

角色一致性：所有生成图片使用统一的角色描述，确保外观一致。
风格约束：写实风格（photorealistic），禁止动漫/二次元。

触发条件：
| 触发词           | 场景       | 概率 |
|-----------------|-----------|------|
| 画/画一个/帮我画   | 主动绘画   | 80%  |
| 自拍/照片/长什么样 | 猫娘自拍   | 30%  |
| 吃饭/美食/饿了    | 猫娘吃饭   | 25%  |
| 睡觉/晚安/困了    | 猫娘睡觉   | 25%  |
| 生日/蛋糕/庆祝    | 庆祝场景   | 25%  |
"""
import asyncio
import hashlib
import os
import random
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Optional

import aiohttp
from nonebot import logger

from .config import IMAGE_CACHE_DIR
from .config import IMAGE_GEN_API_KEY
from .config import IMAGE_GEN_BASE_URL
from .config import IMAGE_GEN_MODEL


def _write_file_sync(path: str, data: bytes):
    """同步写文件（供 asyncio.to_thread 调用）。"""
    with open(path, "wb") as f:
        f.write(data)


# 图片缓存目录
os.makedirs(IMAGE_CACHE_DIR, exist_ok=True)

# ============================================================
# 角色一致性定义（所有图片生成的统一角色描述）
# ============================================================

CHARACTER_DESC = (
    "a young woman with long pink hair, cat ears on top of her head, "
    "amber colored eyes, fair skin, wearing a pink hoodie, petite build, "
    "cute and natural appearance"
)

# 写实风格后缀（追加到每个 prompt 末尾）
REALISTIC_SUFFIX = (
    "photorealistic, realistic photo, 4k, highly detailed, "
    "natural lighting, shot on camera, consistent character, same person"
)

# 负面 prompt（排除动漫风格）
NEGATIVE_PROMPT = (
    "anime, cartoon, illustration, drawing, 2d, manga, "
    "multiple people, different hair color, different face, "
    "deformed, bad anatomy, blurry, low quality"
)

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
        "prompt": (
            f"photorealistic selfie of {CHARACTER_DESC}, "
            "holding a phone taking a mirror selfie, cute natural smile, "
            "indoor bedroom setting, soft natural light, {REALISTIC_SUFFIX}"
        ),
        "scene": "selfie",
    },
    # 生活场景：用户描述场景，bot 配图（25%）
    "eating": {
        "keywords": ["吃饭", "美食", "饿了", "吃东西", "干饭", "午饭", "晚饭", "早饭", "做饭", "好吃的"],
        "prob": 0.25,
        "prompt": (
            f"photorealistic photo of {CHARACTER_DESC}, "
            "eating delicious food happily at a table, cute expression, "
            "warm indoor lighting, cozy restaurant or home setting, {REALISTIC_SUFFIX}"
        ),
        "scene": "eating",
    },
    "sleep": {
        "keywords": ["睡觉", "晚安", "困了", "要睡了", "睡了", "好困"],
        "prob": 0.25,
        "prompt": (
            f"photorealistic photo of {CHARACTER_DESC}, "
            "sleeping peacefully in bed, soft blanket, moonlight through window, "
            "cozy bedroom at night, peaceful expression, {REALISTIC_SUFFIX}"
        ),
        "scene": "sleep",
    },
    "celebrate": {
        "keywords": ["生日", "蛋糕", "庆祝", "节日", "快乐", "纪念"],
        "prob": 0.25,
        "prompt": (
            f"photorealistic photo of {CHARACTER_DESC}, "
            "celebrating with cake and confetti, happy excited expression, "
            "party decorations in background, warm festive lighting, {REALISTIC_SUFFIX}"
        ),
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
    """从用户消息中提取绘画描述，统一追加写实风格和角色一致性。"""
    cleaned = user_msg
    for kw in ["帮我画", "画一个", "画个", "画张", "画", "生成图片", "生成一张"]:
        cleaned = cleaned.replace(kw, "").strip()
    cleaned = re.sub(r'^[，。！？,\s]+|[，。！？,\s]+$', '', cleaned)

    if len(cleaned) < 2:
        return (
            f"photorealistic portrait of {CHARACTER_DESC}, "
            f"cute natural pose, looking at camera, {REALISTIC_SUFFIX}"
        )
    return (
        f"photorealistic photo of {CHARACTER_DESC}, "
        f"{cleaned}, {REALISTIC_SUFFIX}"
    )


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
        "size": "1024x768",
        "negative_prompt": NEGATIVE_PROMPT,
    }

    try:
        logger.info(f"[图片] 正在生成: {prompt[:80]}...")
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

                    await asyncio.to_thread(_write_file_sync, cache_path, img_data)
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
