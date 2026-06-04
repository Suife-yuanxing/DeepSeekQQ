"""主动消息模块：早安/晚安/沉默检测/节日问候。
使用 LLM 基于猫娘人设动态生成个性化消息。"""
import asyncio
import random
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot.adapters.onebot.v11 import Message as OBMessage

from .config import PROACTIVE_CONFIG, MY_QQ
from .database import get_today_proactive_count, log_proactive, get_silent_private_users, get_affection
from .api import call_deepseek_api
from .memory import save_reply
from nonebot import logger

_scheduler: Optional[AsyncIOScheduler] = None
_registered_bot_id: Optional[int] = None


async def _send_proactive_message(bot, target_type: str, target_id: str, message: str):
    try:
        if target_type == "private":
            await bot.send_private_msg(user_id=int(target_id), message=OBMessage(message))
            # 存入对话记忆，让 bot 理解用户回复的上下文
            session_id = f"private_{target_id}"
            await save_reply(session_id, target_id, "[主动消息]", message)
            logger.info(f"[主动消息] 私聊 {target_id}: {message[:50]}...")
        elif target_type == "group":
            await bot.send_group_msg(group_id=int(target_id), message=OBMessage(message))
            logger.info(f"[主动消息] 群聊 {target_id}: {message[:50]}...")
        await log_proactive(target_id, target_type, message)
    except Exception as e:
        logger.error(f"[主动消息] 发送失败 {target_id}: {e}")


async def _generate_proactive_message(scene: str, user_id: str = "") -> str:
    """用 LLM 基于猫娘人设生成个性化主动消息。

    scene: morning/night/silence/holiday
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

    scene_prompts = {
        "morning": "现在是早上，你要给主人发一条早安消息。",
        "night": "现在是深夜，你要给主人发一条晚安消息。",
        "silence": "你好久没和主人聊天了，想主动找他说话。",
        "holiday": "今天是个节日，要给主人发节日问候。",
    }

    prompt = scene_prompts.get(scene, "给主人发一条消息。")
    if affection_info:
        prompt += f"\n{affection_info}"

    sys_prompt = (
        "你是一只猫娘，正在QQ上给你的主人发主动消息。"
        "你的性格：猫系、会调侃、嘴硬心软、偶尔撒娇、有点傲娇、有点小好色。"
        "你对好看的人会多看两眼，偶尔说些暧昧的话，好感度越高越大胆。"
        "规则：\n"
        "1. 1-2句话，短一点，像发QQ消息\n"
        "2. 口语化，自然，不要像写作文\n"
        "3. 不要加括号动作、不要旁白\n"
        "4. 每次语气都不一样，不要重复\n"
        "5. 根据你们的关系远近调整语气（熟人更软更暧昧，生人更懒）\n"
        "6. 可以适当加一些猫娘特色的口癖（喵~、哼、呜）但不要每句都加\n"
        "7. 如果适合，在末尾加 [sticker:情绪]，大约20%概率\n"
        "8. 绝对不要输出 [doge]、[微笑] 等QQ内置表情标签"
    )

    try:
        messages = [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": prompt}
        ]
        msg = await call_deepseek_api(messages, temperature=1.0)
        msg = msg.strip().strip('"').strip("'")
        # 去掉动作描写
        import re
        msg = re.sub(r'[（(][^）)]*[）)]', '', msg).strip()
        if len(msg) > 5:
            return msg
    except Exception as e:
        logger.error(f"[主动消息] LLM生成失败: {e}")

    # fallback
    fallbacks = {
        "morning": ["早呀~", "喵~早安", "起床了吗？"],
        "night": ["晚安喵~", "该睡了哦", "晚安，做个好梦"],
        "silence": ["你在干嘛呀~", "好久不见了喵", "想你了"],
        "holiday": ["节日快乐喵~", "今天过节呀~"],
    }
    return random.choice(fallbacks.get(scene, ["喵~"]))


async def _morning_greeting(bot):
    cfg = PROACTIVE_CONFIG["morning_greeting"]
    if not cfg["enabled"]:
        return
    for uid in cfg["target_users"]:
        msg = await _generate_proactive_message("morning", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg)
    for gid in cfg["target_groups"]:
        msg = await _generate_proactive_message("morning")
        await _send_proactive_message(bot, "group", str(gid), msg)


async def _night_greeting(bot):
    cfg = PROACTIVE_CONFIG["night_greeting"]
    if not cfg["enabled"]:
        return
    for uid in cfg["target_users"]:
        msg = await _generate_proactive_message("night", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg)
    for gid in cfg["target_groups"]:
        msg = await _generate_proactive_message("night")
        await _send_proactive_message(bot, "group", str(gid), msg)


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
            msg = await _generate_proactive_message("silence", user_id)
            await _send_proactive_message(bot, "private", user_id, msg)
            await asyncio.sleep(random.uniform(2, 5))
    except Exception as e:
        logger.info(f"[主动消息] 沉默检查失败: {e}")


async def _holiday_greeting(bot):
    cfg = PROACTIVE_CONFIG["holiday_greeting"]
    if not cfg["enabled"]:
        return
    today = datetime.now().strftime("%m-%d")
    if today in cfg["holidays"]:
        holiday_name = cfg["holidays"][today]  # fallback
        for uid in cfg["target_users"]:
            msg = await _generate_proactive_message("holiday", str(uid))
            await _send_proactive_message(bot, "private", str(uid), msg)
        for gid in cfg["target_groups"]:
            msg = await _generate_proactive_message("holiday")
            await _send_proactive_message(bot, "group", str(gid), msg)


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
        _scheduler.add_job(_morning_greeting, 'cron', hour=mg["hour"], minute=mg["minute"], args=[bot], id="morning", replace_existing=True)

    ng = PROACTIVE_CONFIG["night_greeting"]
    if ng["enabled"]:
        _scheduler.add_job(_night_greeting, 'cron', hour=ng["hour"], minute=ng["minute"], args=[bot], id="night", replace_existing=True)

    sc = PROACTIVE_CONFIG["silence_check"]
    if sc["enabled"]:
        _scheduler.add_job(_check_silence_and_notify, 'interval', hours=sc["check_interval_hours"], args=[bot], id="silence", replace_existing=True)

    hg = PROACTIVE_CONFIG["holiday_greeting"]
    if hg["enabled"]:
        _scheduler.add_job(_holiday_greeting, 'cron', hour=0, minute=1, args=[bot], id="holiday", replace_existing=True)

    _scheduler.start()
    logger.info(f"✅ 主动消息已启动 | 早安:{mg['hour']}:{mg['minute']:02d} | 晚安:{ng['hour']}:{ng['minute']:02d} | 沉默检查:每{sc['check_interval_hours']}h(阈值{sc['silence_threshold_hours']}h) | 节日:每天0:01")


async def shutdown_proactive():
    global _scheduler, _registered_bot_id
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        _registered_bot_id = None
        logger.info("✅ 主动消息调度器已关闭")
