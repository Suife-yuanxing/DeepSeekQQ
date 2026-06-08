"""表情包管理脚本。

功能：
1. 搜索可爱/傲娇/搞笑表情包
2. 下载到本地目录供用户检查
3. 生成标签文件
4. 部署到服务器表情包目录

使用方法：
1. 运行脚本搜索表情包
2. 检查下载的表情包
3. 运行部署命令上传到服务器
"""

import os
import json
import asyncio
import aiohttp
from pathlib import Path
from typing import List, Dict, Any
import logging

# 配置
LOCAL_CHECK_DIR = r"D:\SUIFENG\Pictures\stickers_check"
STICKER_CATEGORIES = {
    "cute": {
        "name": "可爱软萌",
        "keywords": ["可爱", "萌", "卖萌", "软萌", "猫咪", "小猫", "猫娘"],
        "description": "可爱、软萌、让人想摸摸的表情包"
    },
    "tsundere": {
        "name": "傲娇毒舌",
        "keywords": ["傲娇", "嘴硬", "哼", "才不是", "讨厌", "笨蛋"],
        "description": "傲娇、嘴硬、毒舌的表情包"
    },
    "funny": {
        "name": "搞笑日常",
        "keywords": ["搞笑", "好笑", "吐槽", "哈哈哈", "笑死", "无语"],
        "description": "搞笑、吐槽、日常的表情包"
    }
}

# 表情包来源（示例，实际需要根据具体API调整）
STICKER_SOURCES = [
    {
        "name": "斗图啦",
        "url": "https://www.doutula.com/api/search",
        "params": {"keyword": "{keyword}", "page": 1, "page_size": 20}
    },
    {
        "name": "表情包在线",
        "url": "https://www.bqb7.com/api/search",
        "params": {"q": "{keyword}", "page": 1}
    }
]


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def search_stickers(keyword: str, limit: int = 20) -> List[Dict[str, Any]]:
    """搜索表情包。

    Args:
        keyword: 搜索关键词
        limit: 返回数量限制

    Returns:
        表情包列表，每个元素包含 url、title、source 等信息
    """
    results = []

    async with aiohttp.ClientSession() as session:
        for source in STICKER_SOURCES:
            try:
                # 构建请求参数
                params = source["params"].copy()
                for key, value in params.items():
                    if isinstance(value, str) and "{keyword}" in value:
                        params[key] = value.format(keyword=keyword)

                # 发送请求
                async with session.get(
                    source["url"],
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(f"[{source['name']}] 请求失败: {resp.status}")
                        continue

                    data = await resp.json()

                    # 解析结果（根据具体API调整）
                    if source["name"] == "斗图啦":
                        items = data.get("data", {}).get("list", [])
                        for item in items[:limit]:
                            results.append({
                                "url": item.get("image", ""),
                                "title": item.get("title", ""),
                                "source": source["name"],
                                "tags": [keyword]
                            })
                    elif source["name"] == "表情包在线":
                        items = data.get("data", [])
                        for item in items[:limit]:
                            results.append({
                                "url": item.get("url", ""),
                                "title": item.get("title", ""),
                                "source": source["name"],
                                "tags": [keyword]
                            })

            except Exception as e:
                logger.error(f"[{source['name']}] 搜索失败: {e}")

    return results[:limit]


async def download_sticker(url: str, save_dir: str, filename: str = None) -> str:
    """下载表情包到本地。

    Args:
        url: 表情包URL
        save_dir: 保存目录
        filename: 文件名（可选）

    Returns:
        保存的文件路径
    """
    if not filename:
        # 从URL提取文件名
        filename = url.split("/")[-1].split("?")[0]
        if not filename.endswith(('.jpg', '.jpeg', '.png', '.gif')):
            filename = f"sticker_{hash(url) % 10000}.jpg"

    os.makedirs(save_dir, exist_ok=True)
    filepath = os.path.join(save_dir, filename)

    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
            if resp.status == 200:
                with open(filepath, 'wb') as f:
                    f.write(await resp.read())
                logger.info(f"下载成功: {filename}")
                return filepath
            else:
                logger.error(f"下载失败: {url} (status={resp.status})")
                return ""


async def search_and_download_category(category: str, limit: int = 10) -> List[str]:
    """搜索并下载指定分类的表情包。

    Args:
        category: 分类名称（cute/tsundere/funny）
        limit: 每个关键词下载数量

    Returns:
        下载的文件路径列表
    """
    if category not in STICKER_CATEGORIES:
        logger.error(f"未知分类: {category}")
        return []

    cat_info = STICKER_CATEGORIES[category]
    save_dir = os.path.join(LOCAL_CHECK_DIR, category)
    downloaded = []

    for keyword in cat_info["keywords"]:
        logger.info(f"搜索 [{cat_info['name']}] 关键词: {keyword}")

        # 搜索
        results = await search_stickers(keyword, limit=5)
        if not results:
            logger.warning(f"未找到结果: {keyword}")
            continue

        # 下载
        for result in results[:limit]:
            if result["url"]:
                filepath = await download_sticker(result["url"], save_dir)
                if filepath:
                    downloaded.append(filepath)

        # 避免请求过快
        await asyncio.sleep(1)

    logger.info(f"[{cat_info['name']}] 下载完成: {len(downloaded)} 个")
    return downloaded


def generate_tags_file(sticker_dir: str, output_file: str = None) -> None:
    """为表情包目录生成标签文件。

    Args:
        sticker_dir: 表情包目录
        output_file: 输出文件路径（默认为目录下的 sticker_tags.json）
    """
    if not output_file:
        output_file = os.path.join(sticker_dir, "sticker_tags.json")

    tags = {}
    supported_ext = {'.jpg', '.jpeg', '.png', '.gif'}

    for filename in os.listdir(sticker_dir):
        filepath = os.path.join(sticker_dir, filename)
        if not os.path.isfile(filepath):
            continue

        ext = os.path.splitext(filename)[1].lower()
        if ext not in supported_ext:
            continue

        # 根据目录名推断标签
        parent_dir = os.path.basename(os.path.dirname(filepath))
        if parent_dir in STICKER_CATEGORIES:
            tags[filename] = {
                "tags": [parent_dir],
                "scenes": STICKER_CATEGORIES[parent_dir]["keywords"][:3]
            }
        else:
            tags[filename] = {
                "tags": ["default"],
                "scenes": []
            }

    # 写入标签文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(tags, f, ensure_ascii=False, indent=2)

    logger.info(f"生成标签文件: {output_file} ({len(tags)} 个表情包)")


async def main():
    """主函数：搜索并下载所有分类的表情包。"""
    logger.info("开始搜索和下载表情包...")

    all_downloaded = []
    for category in STICKER_CATEGORIES:
        downloaded = await search_and_download_category(category, limit=5)
        all_downloaded.extend(downloaded)

    logger.info(f"总共下载: {len(all_downloaded)} 个表情包")
    logger.info(f"保存目录: {LOCAL_CHECK_DIR}")
    logger.info("请检查下载的表情包，然后运行部署命令上传到服务器")

    # 生成标签文件（供检查用）
    for category in STICKER_CATEGORIES:
        category_dir = os.path.join(LOCAL_CHECK_DIR, category)
        if os.path.exists(category_dir):
            generate_tags_file(category_dir)


if __name__ == "__main__":
    asyncio.run(main())
