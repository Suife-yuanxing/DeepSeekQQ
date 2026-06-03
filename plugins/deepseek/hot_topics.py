"""热点话题主动推送模块。

功能：
- 定期抓取热搜/热点话题
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
# 热搜抓取
# ============================================================

# 微博热搜 RSS（第三方）
_WEIBO_RSS_URL = "https://rsshub.app/weibo/search/hot"
# 备用：tophub.today
_TOPHUB_URL = "https://api.vvhan.com/api/hotlist/wbHot"

# 敏感词过滤
_SENSITIVE_KEYWORDS = [
    "政治", "军事", "战争", "死亡", "事故", "灾害", "暴力",
    "色情", "赌博", "毒品", "诈骗", "恐怖", "自杀", "抗议",
    "官员", "政府", "法院", "警察", "逮捕", "枪击",
]


async def fetch_trending() -> List[HotTopic]:
    """获取热搜话题列表。"""
    topics = []

    # 方案1: 韩小韩API（微博热搜）
    try:
        session = await get_http_session()
        async with session.get(_TOPHUB_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                data = await resp.json()
                if data.get("success") and data.get("data"):
                    for item in data["data"][:30]:
                        title = item.get("title", "").strip()
                        hot = item.get("hot", "")
                        url = item.get("url", "")
                        if title and len(title) > 2:
                            topics.append(HotTopic(title=title, hot=str(hot), url=url, category="微博"))
                    if topics:
                        logger.info(f"[热搜] 韩小韩API获取 {len(topics)} 条")
                        return topics
    except Exception as e:
        logger.warning(f"[热搜] 韩小韩API失败: {e}")

    # 方案2: 直接搜索今日热点（使用更精准的查询）
    try:
        from .search import search
        queries = ["今天微博热搜 最新", "今日新闻热点", "今天发生的大事"]
        for query in queries:
            result = await search(query, max_results=5)
            if result and result.results:
                for item in result.results[:10]:
                    title = item.get("title", "").strip()
                    url = item.get("url", "")
                    if title and len(title) > 3:
                        # 从标题中清理常见后缀
                        clean_title = re.sub(r'\s*[-_|].*$', '', title).strip()
                        if len(clean_title) > 3:
                            title = clean_title
                        topics.append(HotTopic(title=title, url=url, category="搜索"))
                if topics:
                    logger.info(f"[热搜] 搜索获取 {len(topics)} 条 (query: {query})")
                    return topics
    except Exception as e:
        logger.warning(f"[热搜] 搜索获取失败: {e}")

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
    # 低质量关键词：聚合站、导航站、非具体话题
    _LOW_QUALITY_KEYWORDS = [
        "热榜", "热搜榜", "热榜官网", "导航", "聚合",
        "合集", "资源合集", "工具合集", "模板合集",
        "官网", "首页", "入口", "大全", "汇总",
        "排行榜", "排名", "top50", "top100",
    ]
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
        if any(kw in title for kw in _LOW_QUALITY_KEYWORDS):
            logger.debug(f"[热搜] 过滤低质量: {t.title}")
            continue
        # 过滤标题中包含大量特殊符号的
        special_count = sum(1 for c in t.title if not c.isalnum() and not '一' <= c <= '鿿' and c not in ' -')
        if special_count > 3:
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
            {"role": "system", "content": "你是一只猫娘少女，正在刷手机看到有趣的东西想分享给朋友。用QQ聊天的语气，口语化，短句。只输出消息内容，不要任何其他文字。"},
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
