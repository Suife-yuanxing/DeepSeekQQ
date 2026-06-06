"""主动消息模块：早安/晚安/沉默检测/节日问候。
使用 LLM 基于猫娘人设动态生成个性化消息。"""
import asyncio
import random
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
)
from .api import call_deepseek_api
from .memory import save_reply
from .utils import filter_novel_actions
from .sticker import parse_sticker_tag
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

    scene_prompts = {
        "morning": "现在是早上，你要给主人发一条早安消息。语气要自然，像刚睡醒一样，不要像客服。",
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


async def _morning_greeting(bot):
    """9:30 兜底早安：如果用户今天已被感知式早安覆盖，跳过。"""
    cfg = PROACTIVE_CONFIG["morning_greeting"]
    if not cfg["enabled"]:
        return
    for uid in cfg["target_users"]:
        # 已发过早安或已被感知式触发，跳过
        if await has_proactive_today(str(uid), "morning") or \
           await has_proactive_today(str(uid), "morning_triggered"):
            continue
        session_id = f"private_{uid}"
        from .database import has_user_message_today
        if await has_user_message_today(session_id):
            continue
        msg = await _generate_proactive_message("morning", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg, scene="morning")
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

            # P1: 沉默上下文 — 获取上次对话摘要
            ctx = await get_last_conversation_context(user_id)
            if ctx:
                logger.info(
                    f"[主动消息] 沉默上下文: 用户{user_id[:6]} 上次聊: {ctx['topic'][:20]} "
                    f"({int(ctx['hours_ago'])}h前)"
                )

            # P1: 情绪驱动 — 检查 bot 情绪是否适合主动联系
            mood_boost = await _get_mood_driven_boost()
            if mood_boost < 1.0 and random.random() > mood_boost:
                logger.debug(f"[情绪驱动] 沉默检查跳过 (boost={mood_boost})")
                continue

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
        _scheduler.add_job(_morning_greeting, 'cron', hour=mg["hour"], minute=mg["minute"], args=[bot], id="morning", replace_existing=True, jitter=300)

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
