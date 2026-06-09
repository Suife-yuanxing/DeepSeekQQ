"""表情包联网检索模块。

功能：
- 当本地表情包库没有合适匹配时，使用 Tavily API 搜索网络表情包
- 从搜索结果中提取图片URL
- 下载并缓存到本地
- 自动添加标签到 sticker_tags.json
- 表情包分类管理
"""
import asyncio
import json
import os
import random
import re
import time
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional

import aiofiles
import aiohttp
from nonebot import logger

from .api import get_http_session
from .config import STICKER_DIR
from .config import TAVILY_API_KEY

# 下载缓存目录
DOWNLOAD_DIR = os.path.join(STICKER_DIR, "downloaded")

# 文件写入锁（防止并发写 sticker_tags.json 导致数据损坏）
_sticker_write_lock = asyncio.Lock()

# 情绪对应的搜索关键词
_EMOTION_SEARCH_KEYWORDS = {
    "happy": ["开心表情包", "快乐表情包", "哈哈表情包"],
    "angry": ["生气表情包", "愤怒表情包", "哼表情包"],
    "shy": ["害羞表情包", "脸红表情包", "扭捏表情包"],
    "sad": ["难过表情包", "伤心表情包", "哭的表情包"],
    "tsundere": ["傲娇表情包", "嘴硬表情包", "才不是表情包"],
    "cute": ["可爱表情包", "萌萌哒表情包", "卖萌表情包"],
    "funny": ["搞笑表情包", "沙雕表情包", "笑死表情包"],
    "love": ["恋爱表情包", "爱心表情包", "喜欢你表情包"],
    "speechless": ["无语表情包", "翻白眼表情包", "服了表情包"],
    "excited": ["兴奋表情包", "激动表情包", "冲鸭表情包"],
    "default": ["猫娘表情包", "可爱猫猫表情包"],
}

# 搜索结果缓存（避免重复搜索）
_search_cache: Dict[str, List[str]] = {}
_CACHE_TTL = 3600  # 1小时缓存

# 每日下载计数（防止过度下载）
_daily_downloads: int = 0
_daily_date: str = ""
MAX_DAILY_DOWNLOADS = 20


async def search_sticker_online(emotion: str) -> Optional[str]:
    """联网搜索表情包，返回下载后的本地路径。

    流程：
    1. 检查缓存
    2. 用 Tavily 搜索
    3. 提取图片URL
    4. 下载到本地
    5. 自动添加标签
    """
    global _daily_downloads, _daily_date

    if not TAVILY_API_KEY:
        return None

    # 检查每日限额
    today = time.strftime("%Y-%m-%d")
    if today != _daily_date:
        _daily_date = today
        _daily_downloads = 0
    if _daily_downloads >= MAX_DAILY_DOWNLOADS:
        logger.info("[表情包搜索] 今日下载已达上限")
        return None

    # 确保下载目录存在
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # 检查缓存
    cache_key = emotion
    if cache_key in _search_cache:
        cached_urls = _search_cache[cache_key]
        if cached_urls:
            url = cached_urls.pop(0)
            local_path = await _download_image(url, emotion)
            if local_path:
                return local_path

    # 搜索
    keywords = _EMOTION_SEARCH_KEYWORDS.get(emotion, _EMOTION_SEARCH_KEYWORDS["default"])
    query = random.choice(keywords)

    image_urls = await _tavily_search_images(query)
    if not image_urls:
        logger.info(f"[表情包搜索] 未找到结果: {query}")
        return None

    # 缓存剩余URL
    _search_cache[cache_key] = image_urls[1:]

    # 下载第一张
    url = image_urls[0]
    local_path = await _download_image(url, emotion)
    if local_path:
        _daily_downloads += 1
        # 自动添加标签（从 emotion 中提取 scene 如果有的话）
        parts = emotion.split()
        tag_emotion = parts[0] if parts else emotion
        tag_scene = parts[1] if len(parts) > 1 else ""
        await _add_tag(os.path.basename(local_path), tag_emotion, tag_scene)
        logger.info(f"[表情包搜索] 下载成功: {emotion} -> {os.path.basename(local_path)}")

    return local_path


async def _tavily_search_images(query: str) -> List[str]:
    """用 Tavily 搜索图片。"""
    try:
        session = await get_http_session()
        payload = {
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": 5,
            "include_images": True,
        }
        async with session.post(
            "https://api.tavily.com/search",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status != 200:
                logger.warning(f"[表情包搜索] Tavily API 错误: {resp.status}")
                return []
            data = await resp.json()

        # 提取图片URL
        images = data.get("images", [])
        # 过滤：只保留看起来是表情包的图片（排除网站logo等）
        valid_images = []
        for img_url in images:
            if not isinstance(img_url, str):
                continue
            # 必须是图片URL
            if not any(ext in img_url.lower() for ext in [".jpg", ".jpeg", ".png", ".gif", ".webp"]):
                continue
            # 排除明显的非表情包图片
            if any(skip in img_url.lower() for skip in ["logo", "icon", "avatar", "favicon", "banner"]):
                continue
            valid_images.append(img_url)

        return valid_images[:5]
    except Exception as e:
        logger.error(f"[表情包搜索] Tavily 搜索异常: {e}")
        return []


async def _download_image(url: str, emotion: str) -> Optional[str]:
    """下载图片到本地。"""
    try:
        # 确定文件扩展名
        ext = ".jpg"
        for e in [".gif", ".png", ".webp", ".jpeg", ".jpg"]:
            if e in url.lower():
                ext = e
                break

        # 生成文件名
        filename = f"web_{emotion}_{int(time.time() * 1000)}_{random.randint(1000, 9999)}{ext}"
        save_path = os.path.join(DOWNLOAD_DIR, filename)

        session = await get_http_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            if resp.status != 200:
                return None
            data = await resp.read()
            # 验证：至少5KB，最多2MB
            if len(data) < 5000 or len(data) > 2 * 1024 * 1024:
                return None
            async with aiofiles.open(save_path, "wb") as f:
                await f.write(data)

        if os.path.exists(save_path) and os.path.getsize(save_path) > 5000:
            return save_path
        return None
    except Exception as e:
        logger.error(f"[表情包搜索] 下载失败: {e}")
        return None


async def _add_tag(filename: str, emotion: str, scene: str = ""):
    """自动添加标签到 sticker_tags.json（v2 格式，带并发锁）。"""
    tag_file = os.path.join(STICKER_DIR, "sticker_tags.json")
    async with _sticker_write_lock:
        try:
            tags = {}
            if os.path.exists(tag_file):
                async with aiofiles.open(tag_file, 'r', encoding='utf-8') as f:
                    content = await f.read()
                    tags = json.loads(content)

            # 如果已存在，不覆盖
            if filename in tags:
                return

            # v2 格式
            scenes = [scene] if scene else ["日常"]
            tags[filename] = {"tags": [emotion], "scenes": scenes}

            async with aiofiles.open(tag_file, 'w', encoding='utf-8') as f:
                await f.write(json.dumps(tags, ensure_ascii=False, indent=2))

            logger.info(f"[表情包搜索] 添加标签: {filename} -> {emotion}|{scene}")
        except Exception as e:
            logger.error(f"[表情包搜索] 添加标签失败: {e}")


async def cleanup_old_downloads(max_age_days: int = 7):
    """清理过期的下载缓存。"""
    try:
        if not os.path.exists(DOWNLOAD_DIR):
            return

        now = time.time()
        cutoff = now - max_age_days * 86400
        removed = 0

        for f in os.listdir(DOWNLOAD_DIR):
            fp = os.path.join(DOWNLOAD_DIR, f)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                removed += 1

        if removed:
            logger.info(f"[表情包搜索] 清理了 {removed} 个过期下载")
    except Exception as e:
        logger.error(f"[表情包搜索] 清理失败: {e}")
