"""主动消息模块：早安/晚安/沉默检测/节日问候。"""
import asyncio
import random
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot.adapters.onebot.v11 import Message as OBMessage

from .config import PROACTIVE_CONFIG
from .database import get_today_proactive_count, log_proactive, get_silent_private_users

_scheduler: Optional[AsyncIOScheduler] = None
_registered_bot_id: Optional[int] = None


async def _send_proactive_message(bot, target_type: str, target_id: str, message: str):
    try:
        if target_type == "private":
            await bot.send_private_msg(user_id=int(target_id), message=OBMessage(message))
            print(f"[主动消息] 私聊 {target_id}: {message[:30]}...")
        elif target_type == "group":
            await bot.send_group_msg(group_id=int(target_id), message=OBMessage(message))
            print(f"[主动消息] 群聊 {target_id}: {message[:30]}...")
        await log_proactive(target_id, target_type, message)
    except Exception as e:
        print(f"[主动消息] 发送失败 {target_id}: {e}")


async def _morning_greeting(bot):
    cfg = PROACTIVE_CONFIG["morning_greeting"]
    if not cfg["enabled"]:
        return
    msg = random.choice(cfg["messages"])
    for uid in cfg["target_users"]:
        await _send_proactive_message(bot, "private", str(uid), msg)
    for gid in cfg["target_groups"]:
        await _send_proactive_message(bot, "group", str(gid), msg)


async def _night_greeting(bot):
    cfg = PROACTIVE_CONFIG["night_greeting"]
    if not cfg["enabled"]:
        return
    msg = random.choice(cfg["messages"])
    for uid in cfg["target_users"]:
        await _send_proactive_message(bot, "private", str(uid), msg)
    for gid in cfg["target_groups"]:
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
            msg = random.choice(cfg["messages"])
            await _send_proactive_message(bot, "private", user_id, msg)
            await asyncio.sleep(random.uniform(2, 5))
    except Exception as e:
        print(f"[主动消息] 沉默检查失败: {e}")


async def _holiday_greeting(bot):
    cfg = PROACTIVE_CONFIG["holiday_greeting"]
    if not cfg["enabled"]:
        return
    today = datetime.now().strftime("%m-%d")
    if today in cfg["holidays"]:
        msg = cfg["holidays"][today]
        for uid in cfg["target_users"]:
            await _send_proactive_message(bot, "private", str(uid), msg)
        for gid in cfg["target_groups"]:
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
    print(f"✅ 主动消息已启动 | 早安:{mg['hour']}:{mg['minute']:02d} | 晚安:{ng['hour']}:{ng['minute']:02d} | 沉默检查:每{sc['check_interval_hours']}h(阈值{sc['silence_threshold_hours']}h) | 节日:每天0:01")


async def shutdown_proactive():
    global _scheduler, _registered_bot_id
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        _registered_bot_id = None
        print("✅ 主动消息调度器已关闭")
