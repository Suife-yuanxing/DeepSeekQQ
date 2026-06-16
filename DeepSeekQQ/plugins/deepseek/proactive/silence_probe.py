"""静默探测模块：沉默检测 + 热搜破冰。"""
import asyncio
import random
import re
import time
from datetime import datetime

from nonebot import logger
from nonebot.adapters.onebot.v11 import Message
from nonebot.adapters.onebot.v11 import MessageSegment

from .. import hot_topics
from ..config import PROACTIVE_CONFIG
from ..database import (
    get_last_conversation_context,
    get_relevant_memory_tags,
    get_silent_private_users,
    get_today_proactive_count,
    has_recent_message,
)
from ..memory import save_reply
from .shared import (
    _generate_proactive_message,
    _get_mood_driven_boost,
    _send_proactive_message,
)

# ---------- P2: 热搜破冰（合并到沉默检查） ----------

# 热搜推送限制
_hot_topic_last_push: float = 0
_hot_topic_today_count: int = 0
_hot_topic_today_date: str = ""
_HOT_TOPIC_MAX_DAILY = 3
_HOT_TOPIC_COOLDOWN_HOURS = 4


async def _match_topic_to_user_async(topics: list, user_id: str):
    """异步版本：从热搜列表中选择与用户兴趣最匹配的话题。"""
    try:
        tags = await get_relevant_memory_tags(user_id, limit=5)
        if not tags:
            return None

        # 提取用户兴趣关键词
        interests = []
        for tag in tags:
            content = tag["content"] if hasattr(tag, "keys") else tag[0]
            interests.extend(re.findall(r'[一-鿿]{2,6}', str(content)))

        if not interests:
            return None

        # 在话题标题中找匹配
        best_topic = None
        best_score = 0
        for topic in topics:
            score = sum(1 for kw in interests if kw in topic.title)
            if score > best_score:
                best_score = score
                best_topic = topic

        if best_topic:
            logger.debug(f"[热搜破冰] 兴趣匹配: {best_topic.title[:20]} (score={best_score})")
        return best_topic if best_score > 0 else None
    except Exception:
        return None


async def _try_push_hot_topic(bot, user_id: str, ctx: dict = None) -> bool:
    """尝试用热搜话题作为沉默消息的破冰素材。

    优先级：social_feed新鲜内容 > 热搜匹配 > 上下文 > 通用问候
    Returns: True 表示已发送消息，False 表示无可用内容
    """
    global _hot_topic_last_push, _hot_topic_today_count, _hot_topic_today_date

    # 只在 10:00-22:00 推
    hour = datetime.now().hour
    if hour < 10 or hour >= 22:
        return False

    # 每日限额 + 冷却时间
    today = datetime.now().strftime("%Y-%m-%d")
    if today != _hot_topic_today_date:
        _hot_topic_today_count = 0
        _hot_topic_today_date = today
    if _hot_topic_today_count >= _HOT_TOPIC_MAX_DAILY:
        return False
    if time.time() - _hot_topic_last_push < _HOT_TOPIC_COOLDOWN_HOURS * 3600:
        return False

    try:
        # === 优先从 social_feed 获取新鲜内容 ===
        topic = None
        try:
            from ..social_feed import get_recent_feed
            from ..social_feed import mark_as_mentioned
            from ..social_feed import was_mentioned

            feed_items = get_recent_feed(limit=5, max_age_minutes=240)
            fresh = [
                f for f in feed_items
                if not was_mentioned(f.item_id) and f.relevance > 0.5
            ]
            if fresh:
                item = fresh[0]
                topic = hot_topics.HotTopic(
                    title=item.content,
                    url=item.url,
                    category=item.source,
                )
                mark_as_mentioned(item.item_id)
                logger.info(f"[热搜破冰] 使用Feed内容: {item.content[:30]}")
        except Exception:
            pass

        # === Fallback: 原始热搜获取 ===
        if not topic:
            topics = await hot_topics.fetch_trending()
            if not topics:
                return False
            topics = hot_topics.filter_topics(topics)
            if not topics:
                return False

            # 尝试匹配用户兴趣（从 memory_tags）
            topic = await _match_topic_to_user_async(topics, user_id)
            if not topic:
                topic = random.choice(topics[:10])

        # 生成推送消息（使用更自然的 prompt）
        msg = await hot_topics.generate_push_message(topic)
        if not msg or len(msg) < 5:
            return False

        # 抓取配图
        image_url = await hot_topics.fetch_topic_image(topic.title)
        if image_url:
            topic.image_url = image_url

        # 构建富消息
        rich_msg = Message()
        rich_msg += MessageSegment.text(msg)

        if topic.image_url:
            try:
                local_path = await hot_topics._download_image(topic.image_url)
                if local_path:
                    rich_msg += MessageSegment.text("\n")
                    rich_msg += MessageSegment.image(local_path)
            except Exception:
                pass

        if topic.url:
            rich_msg += MessageSegment.text(f"\n🔗 {topic.url}")

        # 发送
        await bot.send_private_msg(user_id=int(user_id), message=rich_msg)
        session_id = f"private_{user_id}"
        memory_text = f"[热搜推送:{topic.category}] {topic.title}"
        await save_reply(session_id, user_id, "[热搜推送]", memory_text)

        _hot_topic_today_count += 1
        _hot_topic_last_push = time.time()
        logger.info(f"[热搜破冰] 用户{user_id[:6]}: {topic.title[:30]}")
        return True

    except Exception as e:
        logger.debug(f"[热搜破冰] 失败（非关键）: {e}")
        return False


async def _check_silence_and_notify(bot):
    """沉默检测：发现长时间未互动的用户并主动联系。"""
    cfg = PROACTIVE_CONFIG["silence_check"]
    if not cfg["enabled"]:
        return
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    threshold = now.timestamp() - cfg["silence_threshold_hours"] * 3600
    try:
        silent_users = await get_silent_private_users(threshold)
        for user_id in silent_users:
            today_count = await get_today_proactive_count(user_id, today)
            if today_count >= cfg["max_daily_proactive"]:
                continue

            # P2: 活跃检测 — 最近 1 小时有对话就不打扰
            session_id = f"private_{user_id}"
            if await has_recent_message(session_id, minutes=60):
                logger.debug(f"[主动消息] 用户{user_id[:6]} 最近1h活跃，跳过")
                continue

            # P1: 情绪驱动 — 检查 bot 情绪是否适合主动联系
            mood_boost = await _get_mood_driven_boost()
            if mood_boost < 1.0 and random.random() > mood_boost:
                logger.debug(f"[情绪驱动] 沉默检查跳过 (boost={mood_boost})")
                continue

            # P1: 沉默上下文 — 获取上次对话摘要
            ctx = await get_last_conversation_context(user_id)

            # P2: 热搜破冰 — 优先用热搜话题，其次上下文
            topic_used = await _try_push_hot_topic(bot, user_id, ctx)
            if topic_used:
                await asyncio.sleep(random.uniform(2, 5))
                continue

            # 无热搜可用 → 上下文消息或通用问候
            if ctx:
                logger.info(
                    f"[主动消息] 沉默上下文: 用户{user_id[:6]} 上次聊: {ctx['topic'][:20]} "
                    f"({int(ctx['hours_ago'])}h前)"
                )

            msg = await _generate_proactive_message("silence", user_id, context=ctx)
            await _send_proactive_message(bot, "private", user_id, msg, scene="silence")
            await asyncio.sleep(random.uniform(2, 5))
    except Exception as e:
        logger.info(f"[主动消息] 沉默检查失败: {e}")
