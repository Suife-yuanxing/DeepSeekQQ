"""热点话题主动推送模块。

功能：
- 定期抓取热搜/热点话题（抖音/B站/小黑盒）
- 筛选有趣、非敏感的话题
- 以念念口吻主动挑起话题
- 附带话题链接和配图
"""
import hashlib
import os
import random
import re
import ssl
import time
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import aiohttp
from nonebot import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment

from .api import call_deepseek_api
from .api import get_http_session
from .config import MAX_DAILY_PUSH
from .config import MY_QQ
from .config import PUSH_COOLDOWN_HOURS
from .database import get_silent_private_users
from .memory import save_reply

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
# 微博热搜 API（公开接口，无需认证）
_WEIBO_API = "https://weibo.com/ajax/side/hotSearch"

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
        from .circuit_breaker import get_breaker

        async def _do_fetch():
            async with session.get(_DOUYIN_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    return await resp.json()
                return None

        breaker = get_breaker("douyin")
        data = await breaker.call(_do_fetch, fallback=lambda: None) if breaker else await _do_fetch()
        if data:
            for item in data.get("word_list", [])[:15]:
                title = item.get("word", "").strip()
                hot = item.get("hot_value", 0)
                if title and len(title) > 2:
                    topics.append(HotTopic(title=title, hot=f"{hot:,}", category="抖音"))
            logger.info(f"[热搜] 抖音获取 {len(topics)} 条")
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
            ValueError, KeyError, TypeError) as e:
        logger.warning(f"[热搜] 抖音API失败: {e}")
    return topics


async def _fetch_bilibili(session) -> List[HotTopic]:
    """获取B站热搜。"""
    topics = []
    try:
        from .circuit_breaker import get_breaker

        async def _do_fetch():
            async with session.get(_BILIBILI_API, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 200:
                    text = await resp.text()
                    if not text.startswith("{"):
                        logger.warning("[热搜] B站返回非JSON，跳过")
                        return None
                    import json
                    return json.loads(text)
                return None

        breaker = get_breaker("bilibili")
        data = await breaker.call(_do_fetch, fallback=lambda: None) if breaker else await _do_fetch()
        if data:
            trending = data.get("data", {}).get("trending", {})
            for item in trending.get("list", [])[:15]:
                title = item.get("keyword", "").strip()
                hot = item.get("heat_score", 0)
                if title and len(title) > 2:
                    topics.append(HotTopic(title=title, hot=f"{hot:,}", category="B站"))
            logger.info(f"[热搜] B站获取 {len(topics)} 条")
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
            ValueError, KeyError, TypeError) as e:
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
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"[热搜] 小黑盒搜索失败: {e}")
    return topics


async def _fetch_weibo(session) -> List[HotTopic]:
    """获取微博热搜（公开接口，无需认证）。"""
    topics = []
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://weibo.com/",
        }
        async with session.get(
            _WEIBO_API, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 200:
                import json
                text = await resp.text()
                data = json.loads(text)
                # 格式: data["data"]["realtime"] 是实时热搜列表
                realtime = data.get("data", {}).get("realtime", [])
                for item in realtime[:15]:
                    word = item.get("word", "").strip()
                    if not word:
                        # 有时是 "word_scheme" 格式
                        word = item.get("note", "").strip()
                    hot = item.get("num", 0)
                    url = item.get("scheme", "")  # 微博跳转链接
                    if word and len(word) > 2:
                        topics.append(HotTopic(
                            title=word, hot=f"{hot:,}" if hot else "",
                            url=f"https://s.weibo.com/weibo?q={word}" if not url else url,
                            category="微博",
                        ))
                logger.info(f"[热搜] 微博获取 {len(topics)} 条")
    except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError,
            ValueError, KeyError, TypeError) as e:
        logger.warning(f"[热搜] 微博API失败: {e}")
    return topics


async def fetch_trending() -> List[HotTopic]:
    """获取热搜话题列表 - 抖音/B站/微博/小黑盒四源聚合。"""
    topics = []
    session = await get_http_session()

    # 四个源并行获取
    import asyncio
    douyin, bilibili, weibo, xiaoheihe = await asyncio.gather(
        _fetch_douyin(session),
        _fetch_bilibili(session),
        _fetch_weibo(session),
        _fetch_xiaoheihe(),
        return_exceptions=True,
    )

    if isinstance(douyin, list):
        topics.extend(douyin)
    if isinstance(bilibili, list):
        topics.extend(bilibili)
    if isinstance(weibo, list):
        topics.extend(weibo)
    if isinstance(xiaoheihe, list):
        topics.extend(xiaoheihe)

    random.shuffle(topics)
    logger.info(f"[热搜] 共获取 {len(topics)} 条（抖音/B站/微博/小黑盒）")

    # === 存入社交信息流引擎 ===
    if topics:
        try:
            from .social_feed import FeedItem
            from .social_feed import boost_interest_items
            from .social_feed import mark_scrolled
            from .social_feed import store_feed_items

            feed_items = []
            for t in topics:
                item = FeedItem(
                    content=t.title,
                    source=t.category if t.category else "其他",
                    url=t.url,
                    category="",
                )
                feed_items.append(item)

            # 兴趣加权
            feed_items = boost_interest_items(feed_items)
            store_feed_items(feed_items)
            mark_scrolled()
        except (ImportError, AttributeError, ValueError, TypeError) as e:
            logger.debug(f"[热搜] 社交Feed存储失败: {e}")

        # 更新行为引擎的热点缓存（供随机行为引用）
        try:
            from .behavior_engine import update_hot_topic_cache
            update_hot_topic_cache(topics)
        except (ImportError, AttributeError, TypeError) as e:
            logger.debug(f"[热搜] 行为引擎缓存更新失败: {e}")

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
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError) as e:
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
    """用 LLM 生成念念风格的推送消息。"""
    try:
        messages = [
            {"role": "system", "content": "你是林念念，一个21岁的普通女孩，正在刷手机看到有趣的东西想分享给朋友。你的性格：活泼开朗、会调侃、嘴硬、偶尔撒娇、隐藏好色属性。用QQ聊天的语气，口语化，短句。只输出消息内容，不要任何其他文字。不要输出[doge]、[微笑]等QQ内置表情标签。"},
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
# 图片下载（避免 NapCat TLS 证书验证失败）
# ============================================================

def _write_file_sync(path: str, data: bytes):
    """同步写文件（供 asyncio.to_thread 调用）。"""
    with open(path, "wb") as f:
        f.write(data)

_CACHE_DIR = os.path.join("data", "images", "hot_topics")
os.makedirs(_CACHE_DIR, exist_ok=True)

# H-7: 使用 certifi 证书包 + 分级 fallback，替代 ssl.CERT_NONE
try:
    import certifi as _certifi
    _CERTIFI_BUNDLE = _certifi.where()
    _HAS_CERTIFI = True
except ImportError:
    _CERTIFI_BUNDLE = None
    _HAS_CERTIFI = False
    logger.warning("[热搜] certifi 未安装，将使用系统默认证书（可能下载失败）")

# 已知可信的图片源域名（使用严格验证）
_TRUSTED_DOMAINS = {
    "sinaimg.cn", "weibo.com", "weibo.cn",          # 微博
    "hdslb.com", "bilibili.com",                      # B站
    "douyinpic.com", "douyincdn.com",                 # 抖音
    "xhscdn.com", "xiaohongshu.com",                  # 小红书
    "zhimg.com", "zhihu.com",                         # 知乎
}


def _get_ssl_context(url: str) -> ssl.SSLContext:
    """根据 URL 来源分级返回 SSL 上下文。

    已知可信源 → 严格验证（certifi）
    未知源     → 使用 certifi 验证，失败时允许回退
    """
    try:
        hostname = urlparse(url).hostname or ""
        is_trusted = any(hostname.endswith(d) for d in _TRUSTED_DOMAINS)

        if _HAS_CERTIFI:
            ctx = ssl.create_default_context(cafile=_CERTIFI_BUNDLE)
        else:
            ctx = ssl.create_default_context()

        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED if is_trusted else ssl.CERT_REQUIRED
        return ctx
    except (ssl.SSLError, OSError) as e:
        logger.debug(f"[热搜] SSL上下文创建失败: {e}")
        return ssl.create_default_context()


async def _download_image(url: str) -> Optional[str]:
    """下载远程图片到本地缓存目录，返回本地路径。"""
    try:
        # 用 URL hash 做文件名，避免重复下载
        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        elif ".gif" in url.lower():
            ext = ".gif"
        elif ".webp" in url.lower():
            ext = ".webp"
        fname = hashlib.md5(url.encode()).hexdigest()[:12] + ext
        local_path = os.path.join(_CACHE_DIR, fname)

        # 已缓存则直接返回
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path

        session = await get_http_session()
        ssl_ctx = _get_ssl_context(url)
        connector = aiohttp.TCPConnector(ssl=ssl_ctx)
        async with aiohttp.ClientSession(connector=connector) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status == 200:
                    data = await resp.read()
                    if len(data) > 500:  # 过小的可能是错误页
                        import asyncio as _aio
                        await _aio.to_thread(_write_file_sync, local_path, data)
                        logger.debug(f"[热搜] 图片已缓存: {fname} ({len(data)}B)")
                        return local_path
        return None
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ssl.SSLError) as e:
        logger.debug(f"[热搜] 图片下载异常: {e}")
        return None


# ============================================================
# 推送调度
# ============================================================

_last_push_time: float = 0
_today_push_count: int = 0
_last_push_date: str = ""


async def check_and_push_topics(bot) -> None:
    """检查并推送热点话题。由定时任务调用。

    核心改造：不再从热搜列表直接推送，而是：
    1. 获取热搜 → 存入 social_feed
    2. 从 social_feed 选择新鲜内容推送
    3. 触发热梗检测
    """
    global _last_push_time, _today_push_count, _last_push_date

    from datetime import datetime
    from datetime import timedelta
    from datetime import timezone
    now = datetime.now(timezone(timedelta(hours=8)))

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

    # === 热梗自动检测 ===
    try:
        from .meme_detector import detect_new_memes_from_trending
        from .meme_detector import merge_into_lexicon
        import asyncio as _aio

        # 异步检测但不阻塞主流程
        _aio.ensure_future(_detect_and_merge_memes(topics))
    except (ImportError, AttributeError) as e:
        logger.debug(f"[热搜] 热梗检测模块不可用: {e}")

    # 注入热搜标题到行为引擎的微事件池（bot 闲聊时会自然提及）
    try:
        from .behavior_engine import register_micro_events
        event_snippets = [
            f"刚看到热搜「{t.title[:20]}」，有点意思"
            for t in topics[:5] if t.title
        ]
        if event_snippets:
            register_micro_events(event_snippets)
    except (ImportError, AttributeError, TypeError) as e:
        logger.debug(f"[热搜] 行为引擎微事件注册失败: {e}")

    # === 从 social_feed 选择新鲜内容 ===
    try:
        from .social_feed import get_recent_feed
        from .social_feed import mark_as_mentioned
        from .social_feed import was_mentioned

        feed_items = get_recent_feed(limit=10, max_age_minutes=120)
        # 选择未提过的
        fresh = [
            f for f in feed_items
            if not was_mentioned(f.item_id) and f.relevance > 0.5
        ]
        if fresh:
            # 选最相关的一条
            item = fresh[0]
            topic = HotTopic(
                title=item.content,
                url=item.url,
                category=item.source,
            )
            mark_as_mentioned(item.item_id)
        else:
            # fallback: 从原始话题中随机选
            topic = random.choice(topics[:10])
    except (ImportError, AttributeError, ValueError, TypeError) as e:
        logger.debug(f"[热搜] social_feed查询失败: {e}")
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

            # 附带配图（先下载到本地，避免 NapCat TLS 证书验证失败）
            if topic.image_url:
                try:
                    local_path = await _download_image(topic.image_url)
                    if local_path:
                        rich_msg += MessageSegment.text("\n")
                        rich_msg += MessageSegment.image(local_path)
                    else:
                        logger.debug("[热搜] 图片下载失败，跳过配图")
                except (OSError, ValueError) as e:
                    logger.debug(f"[热搜] 图片发送失败: {e}")

            # 附带链接
            if topic.url:
                rich_msg += MessageSegment.text(f"\n🔗 {topic.url}")

            await bot.send_private_msg(user_id=int(user_id), message=rich_msg)
            # 存入对话记忆
            session_id = f"private_{user_id}"
            memory_text = f"[热搜推送:{topic.category}] {topic.title}"
            if topic.url:
                memory_text += f" {topic.url}"
            await save_reply(session_id, str(user_id), "[热搜推送]", memory_text)
            logger.info(f"[热搜] 已推送给 {user_id}: {topic.title[:30]}")
            _today_push_count += 1
            _last_push_time = time.time()
        except Exception as e:
            logger.error(f"[热搜] 推送失败: {e}")

    logger.info(f"[热搜] 今日已推送 {_today_push_count}/{MAX_DAILY_PUSH}")


async def _detect_and_merge_memes(topics: list):
    """后台任务：检测新梗并合并到词库。"""
    try:
        from .meme_detector import detect_new_memes_from_trending
        from .meme_detector import merge_into_lexicon
        from . import meme_lexicon

        new_memes = await detect_new_memes_from_trending(topics)
        if new_memes:
            current_dynamic = getattr(meme_lexicon, 'DYNAMIC_MEMES', [])
            merged = merge_into_lexicon(new_memes, current_dynamic)
            meme_lexicon.DYNAMIC_MEMES = merged
            added_count = len(merged) - len(current_dynamic)
            if added_count > 0:
                logger.info(f"[热搜] 热梗词库已更新: +{added_count} 条，共 {len(merged)} 条动态梗")
    except Exception as e:
        logger.debug(f"[热搜] 热梗检测后台任务异常: {e}")
