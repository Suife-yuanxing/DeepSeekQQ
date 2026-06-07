"""主动消息模块：早安/晚安/沉默检测/节日问候。
使用 LLM 基于猫娘人设动态生成个性化消息。"""
import asyncio
import random
import re
import time
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot.adapters.onebot.v11 import Message as OBMessage

from .config import PROACTIVE_CONFIG, MY_QQ
from .database import (
    get_today_proactive_count, log_proactive, get_silent_private_users,
    get_affection, has_recent_message, get_recent_greetings,
    has_proactive_today, get_today_proactive_count_by_scene,
    get_bot_mood, get_last_conversation_context,
    get_relevant_memory_tags,
)
from .api import call_deepseek_api
from .memory import save_reply
from .utils import filter_novel_actions
from .sticker import parse_sticker_tag
from . import hot_topics
from nonebot import logger

_scheduler: Optional[AsyncIOScheduler] = None
_registered_bot_id: Optional[int] = None


# ---------- P1: 情绪驱动概率调节 ----------

async def _get_mood_driven_boost() -> float:
    """根据 bot 当前情绪状态返回主动消息概率倍数（P1: 情绪驱动）。

    高唤醒时 bot 更想找人说话，低唤醒时懒得动。
    Returns:
        概率倍数: 0.5 ~ 2.0
    """
    try:
        mood = await get_bot_mood()
        arousal = mood.get("arousal", 0.2)
        valence = mood.get("valence", 0.0)
        dominant = mood.get("dominant", "平静")

        # 高唤醒 + 正面情绪 → 兴奋想找人聊天
        if arousal > 0.6 and valence > 0.3:
            logger.debug(f"[情绪驱动] boost=2.0 ({dominant}, arousal={arousal:.2f})")
            return 2.0

        # 高唤醒 + 负面情绪 → 生气/难过想找人倾诉
        if arousal > 0.6 and valence < -0.3:
            logger.debug(f"[情绪驱动] boost=1.5 ({dominant}, arousal={arousal:.2f})")
            return 1.5

        # 中等唤醒 + 正面 → 心情好，略增加
        if arousal > 0.4 and valence > 0.2:
            logger.debug(f"[情绪驱动] boost=1.3 ({dominant}, arousal={arousal:.2f})")
            return 1.3

        # 极低唤醒 → 懒洋洋不想动
        if arousal < 0.15:
            logger.debug(f"[情绪驱动] boost=0.5 ({dominant}, arousal={arousal:.2f})")
            return 0.5

        return 1.0
    except Exception:
        return 1.0


async def _send_proactive_message(bot, target_type: str, target_id: str, message: str, scene: str = ""):
    try:
        if target_type == "private":
            await bot.send_private_msg(user_id=int(target_id), message=OBMessage(message))
            # 存入对话记忆
            session_id = f"private_{target_id}"
            await save_reply(session_id, target_id, "[主动消息]", message)
            logger.info(f"[主动消息] 私聊 {target_id}: {message[:50]}...")
        elif target_type == "group":
            await bot.send_group_msg(group_id=int(target_id), message=OBMessage(message))
            logger.info(f"[主动消息] 群聊 {target_id}: {message[:50]}...")
        await log_proactive(target_id, target_type, message, scene=scene)
    except Exception as e:
        logger.error(f"[主动消息] 发送失败 {target_id}: {e}")


async def _generate_proactive_message(scene: str, user_id: str = "", context: dict = None) -> str:
    """用 LLM 基于猫娘人设生成个性化主动消息。

    scene: morning/night/sleep_nag/silence/holiday/checkin
    context: 沉默上下文（P1），包含 topic/summary/tags/hours_ago
    """
    # 获取好感度信息
    affection_info = ""
    if user_id:
        try:
            aff = await get_affection(user_id)
            score = aff.get("score", 0)
            title = aff.get("title", "陌生人")
            affection_info = f"你和他的关系：{title}（好感度{score}）。"
        except Exception:
            pass

    # 获取最近同类消息用于去重
    recent_same = await get_recent_greetings(scene, 10)
    dedup_hint = ""
    if recent_same:
        dedup_hint = "\n最近发过的消息（绝对不要重复类似风格）：\n" + "\n".join(f"- {m}" for m in recent_same[:5])

    # P1: 沉默上下文 — 携带上次对话摘要
    context_hint = ""
    if scene == "silence" and context:
        topic = context.get("topic", "")
        summary = context.get("summary", "")
        tags = context.get("tags", [])
        hours = context.get("hours_ago", 0)

        if hours < 1:
            time_desc = "刚才"
        elif hours < 8:
            time_desc = f"{int(hours)}小时前"
        elif hours < 24:
            time_desc = "昨天"
        elif hours < 48:
            time_desc = "前天"
        else:
            time_desc = f"{int(hours / 24)}天前"

        context_hint = f"\n你{time_desc}和他聊过「{topic}」。"
        if summary:
            # 从 summary 中提取关键信息
            context_hint += f"上次对话摘要：{summary}。"
        if tags:
            context_hint += f"你知道他感兴趣：{'、'.join(tags[:3])}。"
        context_hint += (
            "\n请基于上次的对话内容自然地找他说话，"
            "比如问问后续、关心一下进展。"
            "不要说「上次」「之前」这样的词，要像自然而然想到的一样。"
        )

    # 早安场景：携带昨晚上下文
    morning_prompt = "现在是早上，你要给主人发一条早安消息。语气要自然，像刚睡醒一样，不要像客服。"
    if scene == "morning" and context:
        mood_hint = context.get("mood_hint", "")
        if mood_hint:
            morning_prompt += f"\n{mood_hint}。"
        # 根据当前时间调整语气
        hour = datetime.now().hour
        if hour < 9:
            morning_prompt += "\n现在比较早，语气可以慵懒一点。"
        elif hour >= 10:
            morning_prompt += "\n现在比较晚了，可以调侃一句'终于醒了？'之类的。"

    scene_prompts = {
        "morning": morning_prompt,
        "night": "现在是深夜，主人还没睡，你要催他睡觉。语气关心但带点命令式，比如'快去睡！'。",
        "sleep_nag": "现在是凌晨了，主人还在聊天。你要催他睡觉，语气要强势一点。",
        "silence": "你好久没和主人聊天了，想主动找他说话。" + (context_hint if context_hint else ""),
        "holiday": "今天是个节日，要给主人发节日问候。",
        "checkin": "你突然想起主人了，想找他说说话。语气随意、自然，像突然想到一样。",
    }

    prompt = scene_prompts.get(scene, "给主人发一条消息。")
    if affection_info:
        prompt += f"\n{affection_info}"
    if dedup_hint:
        prompt += dedup_hint

    sys_prompt = (
        "你是一只猫娘，正在QQ上给你的主人发主动消息。"
        "你的性格：猫系、会调侃、嘴硬心软、偶尔撒娇、有点傲娇、有点小好色。"
        "你对好看的人会多看两眼，偶尔说些暧昧的话，好感度越高越大胆。"
        "规则：\n"
        "1. 1-2句话，短一点，像发QQ消息\n"
        "2. 口语化，自然，不要像写作文\n"
        "3. 不要加括号动作、不要旁白\n"
        "4. 每次语气都不一样，不要重复之前发过的\n"
        "5. 根据你们的关系远近调整语气（熟人更软更暧昧，生人更懒）\n"
        "6. 可以适当加一些猫娘特色的口癖（喵~、哼、呜）但不要每句都加\n"
        "7. 如果适合，在末尾加 [sticker:情绪]，大约20%概率。情绪必须用英文：happy/angry/shy/sad/tsundere/cute/funny/love/speechless/excited\n"
        "8. 绝对不要输出 [doge]、[微笑] 等QQ内置表情标签，绝对不要用中文情绪如[sticker:开心]"
    )

    try:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]
        msg = await call_deepseek_api(messages, temperature=1.0)
        msg = msg.strip().strip('"').strip("'")
        # 去掉动作描写和QQ内置表情标签
        msg = filter_novel_actions(msg)
        # 去掉 [sticker:xxx] 标签（主动消息不发表情包，只保留文字）
        msg, _, _ = parse_sticker_tag(msg)
        if len(msg) > 5:
            return msg
    except Exception as e:
        logger.error(f"[主动消息] LLM生成失败: {e}")

    # fallback
    fallbacks = {
        "morning": ["早呀~", "喵~早安", "起床了吗？"],
        "night": ["快去睡觉！", "都几点了还不睡？", "晚安，赶紧睡！"],
        "sleep_nag": ["你怎么还没睡！！", "再不睡我要生气了", "熬夜对身体不好哦...快去睡"],
        "holiday": ["节日快乐喵~", "今天过节呀~"],
        "checkin": ["在干嘛呀~", "突然想到你了", "忙不忙呀"],
    }
    # P1: 沉默 fallback 根据上下文动态生成
    if scene == "silence" and context:
        topic = context.get("topic", "")
        if topic:
            silence_fallbacks = [
                f"那个{topic}后来怎么样了呀~",
                f"上次聊的{topic}，有新进展吗？",
                f"突然想到{topic}那件事，还好吗？",
                f"在忙什么呀~对了{topic}怎样了？",
            ]
            return random.choice(silence_fallbacks)
    fallbacks.setdefault("silence", ["你在干嘛呀~", "好久不见了喵", "想你了"])
    return random.choice(fallbacks.get(scene, ["喵~"]))


async def _should_send_morning(uid: str) -> dict:
    """判断是否应该发送早安。

    智能逻辑：
    1. 检查昨晚聊天结束时间 → 动态调整
    2. 加入"忘记概率" → 真人不会天天发
    3. 检查上次早安间隔 → 避免过于频繁

    Returns:
        {"should_send": bool, "reason": str, "context": str}
    """
    from .database import (
        get_last_night_end_time, get_last_night_mood_summary,
        get_last_greeting_time, has_user_message_today, has_proactive_today
    )
    from datetime import datetime, timedelta

    session_id = f"private_{uid}"

    # 已发过早安或已被感知式触发，跳过
    if await has_proactive_today(str(uid), "morning") or \
       await has_proactive_today(str(uid), "morning_triggered"):
        return {"should_send": False, "reason": "今日已发过早安", "context": ""}

    # 用户今天已经发消息了，不需要主动早安
    if await has_user_message_today(session_id):
        return {"should_send": False, "reason": "用户已活跃", "context": ""}

    # 检查上次早安间隔（至少间隔20小时）
    last_greeting = await get_last_greeting_time(str(uid), "morning")
    if last_greeting:
        hours_since = (datetime.now().timestamp() - last_greeting) / 3600
        if hours_since < 20:
            return {"should_send": False, "reason": f"距上次早安仅{hours_since:.1f}h", "context": ""}

    # 获取昨晚聊天结束时间
    last_night_end = await get_last_night_end_time(session_id)
    context = ""
    now = datetime.now()

    if last_night_end:
        end_hour = datetime.fromtimestamp(last_night_end).hour
        end_minute = datetime.fromtimestamp(last_night_end).minute
        hours_since_end = (now.timestamp() - last_night_end) / 3600

        # 昨晚聊到很晚（凌晨2点后）→ 今天不主动发早安
        if end_hour >= 2 or (end_hour < 6):
            if hours_since_end < 6:
                return {"should_send": False, "reason": f"昨晚聊到{end_hour}:{end_minute:02d}，太晚了不打扰", "context": ""}

        # 昨晚聊到较晚（0点-2点）→ 早安推迟到10点后
        if 0 <= end_hour < 2:
            if now.hour < 10:
                return {"should_send": False, "reason": f"昨晚聊到{end_hour}:{end_minute:02d}，等晚点再发", "context": ""}

        # 获取昨晚情绪摘要
        mood = await get_last_night_mood_summary(session_id)
        if mood == "negative":
            context = "昨晚用户情绪不太好，早安时可以关心一下"
        elif mood == "positive":
            context = "昨晚聊得开心，早安可以延续好心情"

    # 忘记概率：工作日30%，周末50%
    is_weekend = now.weekday() >= 5
    forget_chance = 0.5 if is_weekend else 0.3
    if random.random() < forget_chance:
        return {"should_send": False, "reason": "随机跳过（模拟忘记）", "context": ""}

    return {"should_send": True, "reason": "条件满足", "context": context}


async def _morning_greeting(bot):
    """智能早安：根据昨晚聊天时间动态调整，加入随机性。"""
    cfg = PROACTIVE_CONFIG["morning_greeting"]
    if not cfg["enabled"]:
        return

    now = datetime.now()
    # 只在合理时间窗口发送（8:00-11:30）
    if not (8 <= now.hour < 12):
        return

    for uid in cfg["target_users"]:
        decision = await _should_send_morning(str(uid))
        if not decision["should_send"]:
            logger.debug(f"[早安] 跳过 {str(uid)[:6]}: {decision['reason']}")
            continue

        # 生成早安消息（携带昨晚上下文）
        msg = await _generate_proactive_message("morning", str(uid), context={"mood_hint": decision["context"]})
        await _send_proactive_message(bot, "private", str(uid), msg, scene="morning")
        logger.info(f"[早安] 发送 {str(uid)[:6]}: {msg[:30]}...")

    for gid in cfg["target_groups"]:
        if await has_proactive_today(str(gid), "morning"):
            continue
        session_id = f"group_{gid}"
        from .database import has_user_message_today
        if await has_user_message_today(session_id):
            continue
        msg = await _generate_proactive_message("morning")
        await _send_proactive_message(bot, "group", str(gid), msg, scene="morning")


async def _night_greeting(bot):
    """00:00 催睡：检查用户最近30分钟有消息才发。"""
    cfg = PROACTIVE_CONFIG["night_greeting"]
    if not cfg["enabled"]:
        return
    for uid in cfg["target_users"]:
        session_id = f"private_{uid}"
        if not await has_recent_message(session_id, minutes=30):
            continue
        msg = await _generate_proactive_message("night", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg, scene="night")
    for gid in cfg["target_groups"]:
        session_id = f"group_{gid}"
        if not await has_recent_message(session_id, minutes=30):
            continue
        msg = await _generate_proactive_message("night")
        await _send_proactive_message(bot, "group", str(gid), msg, scene="night")


# ---------- P2: 热搜破冰（合并到沉默检查） ----------

# 热搜推送限制（原 MAX_DAILY_PUSH / PUSH_COOLDOWN_HOURS）
_hot_topic_last_push: float = 0
_hot_topic_today_count: int = 0
_hot_topic_today_date: str = ""
_HOT_TOPIC_MAX_DAILY = 3
_HOT_TOPIC_COOLDOWN_HOURS = 4


async def _try_push_hot_topic(bot, user_id: str, ctx: dict = None) -> bool:
    """尝试用热搜话题作为沉默消息的破冰素材。

    优先级：热搜 > 上下文 > 通用问候
    Returns: True 表示已发送热搜消息，False 表示无可用热搜
    """
    global _hot_topic_last_push, _hot_topic_today_count, _hot_topic_today_date

    # 只在 10:00-22:00 推热搜
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
        # 获取并过滤热搜
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

        # 生成推送消息
        msg = await hot_topics.generate_push_message(topic)
        if not msg or len(msg) < 5:
            return False

        # 抓取配图
        image_url = await hot_topics.fetch_topic_image(topic.title)
        if image_url:
            topic.image_url = image_url

        # 构建富消息
        from nonebot.adapters.onebot.v11 import MessageSegment, Message
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


async def _check_silence_and_notify(bot):
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


async def _sleep_nag(bot):
    """凌晨催睡：00:00-02:00 每 30 分钟检查，用户还在聊天就催。"""
    hour = datetime.now().hour
    if not (0 <= hour < 2):
        return
    cfg = PROACTIVE_CONFIG.get("sleep_nag", {})
    if not cfg.get("enabled", True):
        return
    max_nags = cfg.get("max_nags_per_night", 2)
    today = datetime.now().strftime("%Y-%m-%d")
    for uid in cfg.get("target_users", []):
        nag_count = await get_today_proactive_count_by_scene(str(uid), "sleep_nag", today)
        if nag_count >= max_nags:
            continue
        session_id = f"private_{uid}"
        if not await has_recent_message(session_id, minutes=30):
            continue
        msg = await _generate_proactive_message("sleep_nag", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg, scene="sleep_nag")


async def _holiday_greeting(bot):
    cfg = PROACTIVE_CONFIG["holiday_greeting"]
    if not cfg["enabled"]:
        return
    today = datetime.now().strftime("%m-%d")
    if today in cfg["holidays"]:
        holiday_name = cfg["holidays"][today]  # fallback
        for uid in cfg["target_users"]:
            msg = await _generate_proactive_message("holiday", str(uid))
            await _send_proactive_message(bot, "private", str(uid), msg, scene="holiday")
        for gid in cfg["target_groups"]:
            msg = await _generate_proactive_message("holiday")
            await _send_proactive_message(bot, "group", str(gid), msg, scene="holiday")


async def register_proactive_jobs(bot):
    """注册主动消息定时任务。支持插件重载后更新 bot 实例。使用 NoneBot 现有事件循环。"""
    global _scheduler, _registered_bot_id
    bot_id = id(bot)
    if _scheduler and _registered_bot_id == bot_id:
        return
    
    if _scheduler:
        try:
            _scheduler.shutdown(wait=True)
        except Exception:
            pass
    
    # 使用 NoneBot 现有的事件循环，避免冲突
    try:
        import nonebot
        loop = nonebot.get_driver().loop
    except Exception:
        loop = asyncio.get_event_loop()
    
    _scheduler = AsyncIOScheduler(event_loop=loop)
    _registered_bot_id = bot_id

    mg = PROACTIVE_CONFIG["morning_greeting"]
    if mg["enabled"]:
        # 智能早安：在多个时间点检查，增加随机性
        # 8:30, 9:30, 10:30 各检查一次，由 _should_send_morning 决定是否发送
        _scheduler.add_job(_morning_greeting, 'cron', hour=8, minute=30, args=[bot], id="morning_1", replace_existing=True, jitter=300)
        _scheduler.add_job(_morning_greeting, 'cron', hour=9, minute=30, args=[bot], id="morning_2", replace_existing=True, jitter=300)
        _scheduler.add_job(_morning_greeting, 'cron', hour=10, minute=30, args=[bot], id="morning_3", replace_existing=True, jitter=300)

    ng = PROACTIVE_CONFIG["night_greeting"]
    if ng["enabled"]:
        _scheduler.add_job(_night_greeting, 'cron', hour=ng["hour"], minute=ng["minute"], args=[bot], id="night", replace_existing=True, jitter=300)

    sc = PROACTIVE_CONFIG["silence_check"]
    if sc["enabled"]:
        _scheduler.add_job(_check_silence_and_notify, 'interval', hours=sc["check_interval_hours"], args=[bot], id="silence", replace_existing=True, jitter=300)

    hg = PROACTIVE_CONFIG["holiday_greeting"]
    if hg["enabled"]:
        _scheduler.add_job(_holiday_greeting, 'cron', hour=0, minute=1, args=[bot], id="holiday", replace_existing=True, jitter=180)

    # Phase 7：随机「突然想到你」问候（每2小时检查，傍晚窗口触发）
    _scheduler.add_job(_random_checkin, 'interval', hours=2, args=[bot], id="random_checkin", replace_existing=True, jitter=300)

    # 凌晨催睡（00:00-01:59 每30分钟检查）
    snc = PROACTIVE_CONFIG.get("sleep_nag", {})
    if snc.get("enabled", True):
        _scheduler.add_job(_sleep_nag, 'cron', hour='0-1', minute='*/30', args=[bot], id="sleep_nag", replace_existing=True, jitter=120)

    _scheduler.start()
    logger.info(f"✅ 主动消息已启动 | 早安:{mg['hour']}:{mg['minute']:02d}(±5min) | 晚安:{ng['hour']}:{ng['minute']:02d}(±5min) | 沉默检查:每{sc['check_interval_hours']}h | 凌晨催睡:00:00-02:00/30min | 节日:每天0:01 | 随机问候:每2h")


# ---------- Phase 7：主动消息增强 ----------

async def _get_proactive_targets() -> list:
    """动态获取主动消息目标用户列表（Phase 7）。

    从最近活跃的私聊会话中自动发现目标，而非硬编码 MY_QQ。
    只对好感度 >= 20（认识的人）的用户发送，最多 10 人。
    """
    try:
        from .database import get_active_sessions, get_affection
        active = await get_active_sessions(hours=168)  # 最近一周
        targets = []
        for sid in active:
            if not sid.startswith("private_"):
                continue
            user_id = sid.replace("private_", "")
            aff = await get_affection(user_id)
            if aff.get("score", 0) >= 20:
                targets.append(user_id)
        logger.info(f"[主动消息] 自动发现 {len(targets)} 个目标用户")
        return targets[:10]
    except Exception:
        return [str(MY_QQ)] if MY_QQ else []


async def _random_checkin(bot):
    """「突然想到你」低频随机问候（Phase 7 + P1 情绪驱动）。

    傍晚时段 2% 基础概率触发，bot 情绪高唤醒时概率翻倍。
    """
    from datetime import datetime
    hour = datetime.now().hour
    # 只在傍晚到晚间窗口触发
    if not (18 <= hour <= 22):
        return

    # P1: 情绪驱动 — 基础概率 × mood_boost
    mood_boost = await _get_mood_driven_boost()
    base_prob = 0.02
    effective_prob = min(base_prob * mood_boost, 0.10)  # 上限 10%

    if random.random() > effective_prob:
        return
    try:
        targets = await _get_proactive_targets()
        if not targets:
            return
        user_id = random.choice(targets)
        msg = await _generate_proactive_message("checkin", user_id)
        await _send_proactive_message(bot, "private", user_id, msg, scene="checkin")
    except Exception as e:
        logger.info(f"[随机问候] 失败（非关键）: {e}")


async def shutdown_proactive():
    global _scheduler, _registered_bot_id
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        _registered_bot_id = None
        logger.info("✅ 主动消息调度器已关闭")
