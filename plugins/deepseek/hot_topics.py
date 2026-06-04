"""热点话题主动推送模块。

功能：
- 定期抓取热搜/热点话题（抖音/B站/小黑盒）
- 筛选有趣、非敏感的话题
- 以猫娘口吻主动挑起话题
- 附带话题链接和配图
"""
import re
import time
import random
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

import aiohttp
from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment, Message

from .config import MY_QQ
from .api import get_http_session, call_deepseek_api
from .database import get_silent_private_users

# ============================================================
# 数据结构
# ============================================================

@dataclass
class HotTopic:
    title: str           # 话题标题
    hot: str = ""        # 热度
    url: str = ""        # 链接
    category: str = ""   # 分类
    image_url: str = ""  # 配图URL


# ============================================================
# 热搜抓取 - 抖音/B站/小黑盒
# ============================================================

# 抖音热搜 API
_DOUYIN_API = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"
# B站热搜 API（不带 wbi 签名）
_BILIBILI_API = "https://api.bilibili.com/x/web-interface/search/square?limit=20"

# 敏感词过滤
_SENSITIVE_KEYWORDS = [
    "政治", "军事", "战争", "死亡", "事故", "灾害", "暴力",
    "色情", "赌博", "毒品", "诈骗", "恐怖", "自杀", "抗议",
    "官员", "政府", "法院", "警察", "逮捕", "枪击",
]


async def _fetch_douyin(session) -> List[HotTopic]:
    """获取抖音热搜。"""
    topics = []
    try:
        async with session.get(_DOUYIN_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                for item in data.get("word_list", [])[:15]:
                    title = item.get("word", "").strip()
                    hot = item.get("hot_value", 0)
                    if title and len(title) > 2:
                        topics.append(HotTopic(title=title, hot=f"{hot:,}", category="抖音"))
                logger.info(f"[热搜] 抖音获取 {len(topics)} 条")
    except Exception as e:
        logger.warning(f"[热搜] 抖音API失败: {e}")
    return topics


async def _fetch_bilibili(session) -> List[HotTopic]:
    """获取B站热搜。"""
    topics = []
    try:
        async with session.get(_BILIBILI_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                text = await resp.text()
                if not text.startswith("{"):
                    logger.warning("[热搜] B站返回非JSON，跳过")
                    return topics
                import json
                data = json.loads(text)
                trending = data.get("data", {}).get("trending", {})
                for item in trending.get("list", [])[:15]:
                    title = item.get("keyword", "").strip()
                    hot = item.get("heat_score", 0)
                    if title and len(title) > 2:
                        topics.append(HotTopic(title=title, hot=f"{hot:,}", category="B站"))
                logger.info(f"[热搜] B站获取 {len(topics)} 条")
    except Exception as e:
        logger.warning(f"[热搜] B站API失败: {e}")
    return topics


async def _fetch_xiaoheihe() -> List[HotTopic]:
    """通过 Tavily 搜索获取小黑盒热门游戏话题。"""
    topics = []
    try:
        from .config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            return topics

        from tavily import AsyncTavilyClient
        client = AsyncTavilyClient(api_key=TAVILY_API_KEY)

        response = await client.search(
            query="小黑盒 今日热门游戏资讯",
            max_results=8,
            search_depth="basic",
        )

        for item in response.get("results", []):
            title = item.get("title", "").strip()
            url = item.get("url", "")
            if title and len(title) > 4:
                topics.append(HotTopic(title=title, url=url, category="小黑盒"))

        logger.info(f"[热搜] 小黑盒搜索获取 {len(topics)} 条")
    except ImportError:
        logger.debug("[热搜] tavily-python 未安装，跳过小黑盒")
    except Exception as e:
        logger.warning(f"[热搜] 小黑盒搜索失败: {e}")
    return topics


async def fetch_trending() -> List[HotTopic]:
    """获取热搜话题列表 - 抖音/B站/小黑盒三源聚合。"""
    topics = []
    session = await get_http_session()

    # 三个源并行获取
    import asyncio
    douyin, bilibili, xiaoheihe = await asyncio.gather(
        _fetch_douyin(session),
        _fetch_bilibili(session),
        _fetch_xiaoheihe(),
        return_exceptions=True,
    )

    if isinstance(douyin, list):
        topics.extend(douyin)
    if isinstance(bilibili, list):
        topics.extend(bilibili)
    if isinstance(xiaoheihe, list):
        topics.extend(xiaoheihe)

    random.shuffle(topics)
    logger.info(f"[热搜] 共获取 {len(topics)} 条（抖音/B站/小黑盒）")
    return topics


async def fetch_topic_image(topic_title: str) -> Optional[str]:
    """为热搜话题抓取一张配图。使用 Tavily 搜索提取图片。"""
    try:
        from .config import TAVILY_API_KEY
        if not TAVILY_API_KEY:
            return None

        from tavily import AsyncTavilyClient
        client = AsyncTavilyClient(api_key=TAVILY_API_KEY)

        response = await client.search(
            query=topic_title,
            max_results=3,
            search_depth="basic",
            include_images=True,
        )

        # 从 images 字段获取
        images = response.get("images", [])
        if images:
            for img in images:
                if isinstance(img, dict):
                    img_url = img.get("url", "")
                else:
                    img_url = str(img)
                if img_url and any(ext in img_url.lower() for ext in [".jpg", ".jpeg", ".png", ".webp", ".gif"]):
                    return img_url

        # 从 results 中提取 og:image 类的图片
        for item in response.get("results", []):
            img_url = item.get("image", "") or item.get("thumbnail", "")
            if img_url:
                return img_url

    except ImportError:
        logger.debug("[热搜] tavily-python 未安装，跳过图片抓取")
    except Exception as e:
        logger.debug(f"[热搜] 图片抓取失败: {e}")

    return None


def filter_topics(topics: List[HotTopic]) -> List[HotTopic]:
    """过滤敏感/低质量话题。"""
    filtered = []
    for t in topics:
        title = t.title.lower()
        # 过滤敏感词
        if any(kw in title for kw in _SENSITIVE_KEYWORDS):
            continue
        # 过滤过短或纯数字
        if len(t.title) < 4 or t.title.isdigit():
            continue
        # 过滤广告
        if any(kw in title for kw in ["广告", "推广", "购买", "优惠券", "折扣"]):
            continue
        # 过滤聚合站/导航站类低质量结果
        _LOW_QUALITY = ["热榜","热搜榜","热榜官网","导航","聚合","合集","资源合集","工具合集","模板合集","官网","首页","入口","大全","汇总","排行榜","排名"]
        if any(kw in t.title.lower() for kw in _LOW_QUALITY):
            continue
        filtered.append(t)
    return filtered


# ============================================================
# 话题推送消息生成
# ============================================================

_PUSH_PROMPT = """你要主动找人聊天，分享一个你刚看到的热搜话题。

话题：{topic}

像刷手机刷到有趣东西随手分享给朋友一样，用你自己的话说。每次开头和语气都不一样，不要每次都用同样的句式。

可以是吐槽、好奇、惊讶、分享欲、想讨论……什么情绪都行。
1-2句，短一点，口语化，像发QQ消息。
不要加括号动作。

你的消息："""

# 多样化的 fallback 消息模板
_FALLBACK_TEMPLATES = [
    "哈哈你看{topic}没，笑死",
    "{topic}你关注了吗？有点意思",
    "刚刷到{topic}，你怎么看",
    "卧槽{topic}也太离谱了吧",
    "{topic}啊...我有点好奇",
    "你听说{topic}了吗",
    "今天{topic}好多人在聊",
    "{topic}这个瓜你吃了没",
    "有个事想跟你聊，{topic}",
    "emmm看到{topic}想问问你",
]


async def generate_push_message(topic: HotTopic) -> str:
    """用 LLM 生成猫娘风格的推送消息。"""
    try:
        messages = [
            {"role": "system", "content": "你是一只猫娘少女，正在刷手机看到有趣的东西想分享给朋友。你的性格：猫系、会调侃、嘴硬、偶尔撒娇、有点小好色。用QQ聊天的语气，口语化，短句。只输出消息内容，不要任何其他文字。不要输出[doge]、[微笑]等QQ内置表情标签。"},
            {"role": "user", "content": _PUSH_PROMPT.format(topic=topic.title)}
        ]
        msg = await call_deepseek_api(messages, temperature=1.0)
        msg = msg.strip().strip('"').strip("'")
        # 去掉可能的动作描写
        msg = re.sub(r'[（(][^）)]*[）)]', '', msg).strip()
        if len(msg) > 100:
            msg = msg[:100]
        if len(msg) > 5:
            return msg
    except Exception as e:
        logger.error(f"[热搜] 生成推送消息失败: {e}")

    # fallback: 随机选一个模板
    template = random.choice(_FALLBACK_TEMPLATES)
    return template.format(topic=topic.title)


# ============================================================
# 推送调度
# ============================================================

_last_push_time: float = 0
_today_push_count: int = 0
_last_push_date: str = ""

MAX_DAILY_PUSH = 3
PUSH_COOLDOWN_HOURS = 4


async def check_and_push_topics(bot) -> None:
    """检查并推送热点话题。由定时任务调用。"""
    global _last_push_time, _today_push_count, _last_push_date

    import pytz
    from datetime import datetime
    now = datetime.now(pytz.timezone('Asia/Shanghai'))

    # 重置每日计数
    today = now.strftime("%Y-%m-%d")
    if today != _last_push_date:
        _today_push_count = 0
        _last_push_date = today

    # 检查限制
    if _today_push_count >= MAX_DAILY_PUSH:
        return

    # 冷却时间
    if time.time() - _last_push_time < PUSH_COOLDOWN_HOURS * 3600:
        return

    # 只在 10:00-22:00 之间推送
    hour = now.hour
    if hour < 10 or hour >= 22:
        return

    # 获取并过滤话题
    topics = await fetch_trending()
    if not topics:
        return

    topics = filter_topics(topics)
    if not topics:
        return

    # 随机选一个
    topic = random.choice(topics[:10])

    # 生成推送消息
    msg = await generate_push_message(topic)

    # 抓取配图
    image_url = await fetch_topic_image(topic.title)
    if image_url:
        topic.image_url = image_url
        logger.info(f"[热搜] 配图: {image_url[:80]}")

    # 推送给主人
    target_users = [MY_QQ] if MY_QQ else []
    for user_id in target_users:
        try:
            # 构建多段消息：文字 → 图片 → 链接
            rich_msg = Message()
            rich_msg += MessageSegment.text(msg)

            # 附带配图
            if topic.image_url:
                try:
                    rich_msg += MessageSegment.text("\n")
                    rich_msg += MessageSegment.image(topic.image_url)
                except Exception as e:
                    logger.debug(f"[热搜] 图片发送失败: {e}")

            # 附带链接
            if topic.url:
                rich_msg += MessageSegment.text(f"\n🔗 {topic.url}")

            await bot.send_private_msg(user_id=int(user_id), message=rich_msg)
            logger.info(f"[热搜] 已推送给 {user_id}: {topic.title[:30]}")
            _today_push_count += 1
            _last_push_time = time.time()
        except Exception as e:
            logger.error(f"[热搜] 推送失败: {e}")

    logger.info(f"[热搜] 今日已推送 {_today_push_count}/{MAX_DAILY_PUSH}")
