"""主动消息模块：早安/晚安/沉默检测/节日问候。

使用 LLM 基于林念念人设动态生成个性化消息。

子模块:
- shared: 共享工具函数（消息生成/发送/目标发现/情绪驱动）
- morning_greeting: 智能早安 + 天气提醒
- night_reminder: 晚安提醒
- silence_probe: 沉默检测 + 热搜破冰
- sleep_nag: 凌晨催睡 + 承诺检查
- holiday_greet: 节日问候
"""
import asyncio
import random
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from nonebot import logger

from ..config import PROACTIVE_CONFIG
from .holiday_greet import _holiday_greeting
from .morning_greeting import _morning_greeting
from .night_reminder import _night_greeting
from .shared import (
    _generate_proactive_message,
    _get_mood_driven_boost,
    _get_proactive_targets,
    _send_proactive_message,
)
from .silence_probe import (
    _HOT_TOPIC_COOLDOWN_HOURS,
    _HOT_TOPIC_MAX_DAILY,
    _check_silence_and_notify,
    _match_topic_to_user_async,
    _try_push_hot_topic,
)
from .sleep_nag import _check_promises
from .sleep_nag import _sleep_nag

_scheduler: Optional[AsyncIOScheduler] = None
_registered_bot_id: Optional[int] = None


# ---------- Phase 7：随机问候 ----------

async def _random_checkin(bot):
    """「突然想到你」低频随机问候（Phase 7 + P1 情绪驱动）。

    傍晚时段 2% 基础概率触发，bot 情绪高唤醒时概率翻倍。
    """
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


# ---------- 人设演化 ----------

def _weekly_personality_eval():
    """人设演化周评估（同步wrapper，由scheduler调用）。"""
    try:
        import asyncio as _asyncio
        from ..personality_drift import weekly_eval_all_users
        _asyncio.create_task(weekly_eval_all_users())
    except Exception:
        pass


# ---------- 注册与关闭 ----------

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
        # 双轨运行（真人化 P1-4）：事件驱动为主，cron 为 fallback
        # 事件驱动路径在 handler 中调用 handle_user_morning() / _morning_event_driven()
        _scheduler.add_job(_morning_greeting, 'cron', hour=8, minute=30, args=[bot], id="morning_1", replace_existing=True, jitter=300)
        _scheduler.add_job(_morning_greeting, 'cron', hour=9, minute=30, args=[bot], id="morning_2", replace_existing=True, jitter=300)
        _scheduler.add_job(_morning_greeting, 'cron', hour=10, minute=30, args=[bot], id="morning_3", replace_existing=True, jitter=300)

    ng = PROACTIVE_CONFIG["night_greeting"]
    if ng["enabled"]:
        # 双轨运行（真人化 P1-4）：事件驱动为主（check_night_event），cron 为 fallback
        _scheduler.add_job(_night_greeting, 'cron', hour=0, minute=0, args=[bot], id="night_0", replace_existing=True, jitter=300)
        _scheduler.add_job(_night_greeting, 'cron', hour=0, minute=30, args=[bot], id="night_30", replace_existing=True, jitter=300)
        _scheduler.add_job(_night_greeting, 'cron', hour=1, minute=0, args=[bot], id="night_60", replace_existing=True, jitter=300)

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

    # 承诺检查：每30分钟检查一次到期承诺
    _scheduler.add_job(_check_promises, 'interval', minutes=30, args=[bot],
                       id="promise_check", jitter=120, replace_existing=True)

    # 人设演化：每周日凌晨3点评估兴趣变化
    _scheduler.add_job(_weekly_personality_eval, 'cron', day_of_week='sun', hour=3, minute=17,
                       id="weekly_personality_eval", jitter=300, replace_existing=True)

    _scheduler.start()
    logger.info(f"✅ 主动消息已启动 | 早安:8:30/9:30/10:30(±5min) | 晚安:00:00/00:30/01:00(±5min) | 沉默检查:每{sc['check_interval_hours']}h | 凌晨催睡:00:00-02:00/30min | 节日:每天0:01 | 随机问候:每2h | 承诺检查:每30min")


async def shutdown_proactive():
    """关闭主动消息调度器。"""
    global _scheduler, _registered_bot_id
    if _scheduler:
        _scheduler.shutdown(wait=True)
        _scheduler = None
        _registered_bot_id = None
        logger.info("✅ 主动消息调度器已关闭")
