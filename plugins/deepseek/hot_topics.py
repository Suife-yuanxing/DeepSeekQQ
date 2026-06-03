"""热点话题主动推送模块。

功能：
- 定期抓取热搜/热点话题
- 筛选有趣、非敏感的话题
- 以猫娘口吻主动挑起话题
"""
import re
import time
import random
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import aiohttp
from nonebot import logger

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

    # 方案2: 直接搜索今日热点
    try:
        from .search import search
        result = await search("今日热搜 热点新闻", max_results=5)
        if result and result.results:
            for item in result.results[:10]:
                title = item.get("title", "").strip()
                if title and len(title) > 3:
                    topics.append(HotTopic(title=title, url=item.get("url", ""), category="搜索"))
            if topics:
                logger.info(f"[热搜] 搜索获取 {len(topics)} 条")
                return topics
    except Exception as e:
        logger.warning(f"[热搜] 搜索获取失败: {e}")

    return topics


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

    # 推送给主人
    target_users = [MY_QQ] if MY_QQ else []
    for user_id in target_users:
        try:
            await bot.send_private_msg(user_id=int(user_id), message=msg)
            logger.info(f"[热搜] 已推送给 {user_id}: {topic.title[:30]}")
            _today_push_count += 1
            _last_push_time = time.time()
        except Exception as e:
            logger.error(f"[热搜] 推送失败: {e}")

    logger.info(f"[热搜] 今日已推送 {_today_push_count}/{MAX_DAILY_PUSH}")
