"""早安问候模块：智能早安 + 天气提醒。"""
import asyncio
import random
from datetime import datetime
from datetime import timedelta
from typing import Optional

from nonebot import logger

from ..config import MY_QQ
from ..config import PROACTIVE_CONFIG
from ..database import (
    get_last_greeting_time,
    get_last_night_end_time,
    get_last_night_mood_summary,
    has_proactive_today,
    has_user_message_today,
)
from ..db_proactive import get_morning_skip_state
from ..db_proactive import set_morning_skip_state
from .shared import (
    _generate_proactive_message,
    _get_proactive_targets,
    _send_proactive_message,
)

# 连续跳过追踪已持久化到 DB（真人化Q3）→ db_proactive.get/set_morning_skip_state


async def _get_weather_alert_for_user(user_id: str) -> Optional[str]:
    """获取恶劣天气提醒（只在恶劣天气时返回提醒文本）。

    恶劣天气包括：暴雨/大雪/寒潮/高温/大风/雾霾
    返回 None 表示天气正常，不需要提醒。
    """
    try:
        from ..config import WEATHER_CITY
        from ..world_context import get_weather

        # 使用默认城市（后续可以扩展为从用户偏好获取）
        city = WEATHER_CITY
        if not city:
            return None

        weather = await get_weather(city)
        if not weather:
            return None

        condition = weather.condition
        temp = weather.temp

        # 判断是否是恶劣天气
        is_severe = False
        alert_type = ""

        # 暴雨/大雨
        if any(kw in condition for kw in ["暴雨", "大雨", "雷阵雨", "大暴雨"]):
            is_severe = True
            alert_type = "rain"
        # 大雪/暴雪
        elif any(kw in condition for kw in ["大雪", "暴雪", "中雪"]):
            is_severe = True
            alert_type = "snow"
        # 高温
        elif temp and temp != "--":
            try:
                temp_val = int(temp)
                if temp_val >= 35:
                    is_severe = True
                    alert_type = "hot"
                elif temp_val <= 0:
                    is_severe = True
                    alert_type = "cold"
            except (ValueError, TypeError):
                pass
        # 大风
        elif weather.wind_scale:
            try:
                if int(weather.wind_scale) >= 6:
                    is_severe = True
                    alert_type = "wind"
            except (ValueError, TypeError):
                pass
        # 雾霾
        elif any(kw in condition for kw in ["雾", "霾", "重度污染"]):
            is_severe = True
            alert_type = "fog"

        if not is_severe:
            return None

        # 生成基于人设的提醒（不要说"暴雨预警"，要像念念一样关心）
        hour = datetime.now().hour

        # 根据时间段调整语气
        if hour < 10:
            time_prefix = "早上好呀~"
        elif hour < 14:
            time_prefix = ""
        else:
            time_prefix = ""

        # 根据恶劣天气类型生成提醒
        weather_msgs = {
            "rain": [
                f"{time_prefix}今天要下大雨诶，你出门记得带伞哦~",
                f"{time_prefix}外面雨好大，出门小心别淋湿了~",
                f"{time_prefix}今天有暴雨呢，能不出门就别出去啦~",
            ],
            "snow": [
                f"{time_prefix}下雪了呢，路滑要小心哦~",
                f"{time_prefix}外面雪好大，出门穿暖和点~",
            ],
            "hot": [
                f"{time_prefix}今天好热呀，记得多喝水别中暑了~",
                f"{time_prefix}外面热死了，出门记得防晒哦~",
            ],
            "cold": [
                f"{time_prefix}外面好冷呀，多穿点衣服别冻着了~",
                f"{time_prefix}今天好冷，出门记得穿厚点~",
            ],
            "wind": [
                f"{time_prefix}风好大，出门小心别被吹跑了~",
                f"{time_prefix}今天风好大，注意安全哦~",
            ],
            "fog": [
                f"{time_prefix}外面有雾霾，出门记得戴口罩~",
                f"{time_prefix}今天能见度低，出门注意安全~",
            ],
        }

        msgs = weather_msgs.get(alert_type, [])
        if msgs:
            return random.choice(msgs)

        return None

    except Exception as e:
        logger.debug(f"[天气提醒] 获取失败（非关键）: {e}")
        return None


async def _should_send_morning(uid: str) -> dict:
    """判断是否应该发送早安。

    智能逻辑：
    1. 检查昨晚聊天结束时间 → 动态调整
    2. 加入"忘记概率" → 真人不会天天发
    3. 检查上次早安间隔 → 避免过于频繁
    4. 连续跳过保护 → 连续2天跳过则第3天强制发送

    Returns:
        {"should_send": bool, "reason": str, "context": str}
    """
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

        # 昨晚聊到很晚（凌晨2-6点）→ 今天不主动发早安
        # Bug 7 修复：原条件 end_hour>=2 or end_hour<6 恒为真，改为 2<=end_hour<6
        if 2 <= end_hour < 6:
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

    # 连续跳过保护：已连续跳过2天 → 第3天强制发送（持久化到DB，真人化Q3）
    skip_state = await get_morning_skip_state(uid)
    skip_count = skip_state["consecutive_skips"]
    if skip_count >= 2:
        await set_morning_skip_state(uid, 0, "")
        logger.info(f"[早安] 连续跳过{skip_count}天，今日强制发送 uid={uid[:6]}")
        return {"should_send": True, "reason": "连续跳过保护（强制发送）", "context": context}

    # 忘记概率：工作日30%，周末50%（连续跳过保护会覆盖此逻辑）
    is_weekend = now.weekday() >= 5
    forget_chance = 0.5 if is_weekend else 0.3
    if random.random() < forget_chance:
        new_skip_count = skip_count + 1
        await set_morning_skip_state(uid, new_skip_count, "")
        return {"should_send": False, "reason": f"随机跳过（连续跳过{new_skip_count}天）", "context": ""}

    # 成功发送，重置计数器
    await set_morning_skip_state(uid, 0, "")
    return {"should_send": True, "reason": "条件满足", "context": context}


async def _morning_greeting(bot):
    """智能早安：根据昨晚聊天时间动态调整，加入随机性。

    多用户支持：从最近活跃会话中自动发现目标用户，而非仅硬编码 MY_QQ。
    """
    cfg = PROACTIVE_CONFIG["morning_greeting"]
    if not cfg["enabled"]:
        return

    now = datetime.now()
    # 只在合理时间窗口发送（8:00-11:30）
    if not (8 <= now.hour < 12):
        return

    # 多用户：从活跃会话自动发现目标
    target_users = await _get_proactive_targets()
    # 确保主人始终在列表中
    if MY_QQ and str(MY_QQ) not in target_users:
        target_users.insert(0, str(MY_QQ))

    for uid in target_users:
        decision = await _should_send_morning(str(uid))
        if not decision["should_send"]:
            logger.debug(f"[早安] 跳过 {str(uid)[:6]}: {decision['reason']}")
            continue

        # 生成早安消息（携带昨晚上下文）
        msg = await _generate_proactive_message("morning", str(uid), context={"mood_hint": decision["context"]})
        await _send_proactive_message(bot, "private", str(uid), msg, scene="morning")
        logger.info(f"[早安] 发送 {str(uid)[:6]}: {msg[:30]}...")

        # 天气提醒：只在恶劣天气时发送（暴雨/寒潮/高温/大风）
        weather_hint = await _get_weather_alert_for_user(str(uid))
        if weather_hint:
            await asyncio.sleep(random.uniform(2, 5))  # 早安后间隔几秒
            await _send_proactive_message(bot, "private", str(uid), weather_hint, scene="weather_alert")
            logger.info(f"[天气提醒] 发送 {str(uid)[:6]}: {weather_hint[:30]}...")

    for gid in cfg["target_groups"]:
        if await has_proactive_today(str(gid), "morning"):
            continue
        session_id = f"group_{gid}"
        if await has_user_message_today(session_id):
            continue
        msg = await _generate_proactive_message("morning")
        await _send_proactive_message(bot, "group", str(gid), msg, scene="morning")


# ═══════════════════════════════════════════════════════════════
# 事件驱动早安 — 真人化 P1-4
# ═══════════════════════════════════════════════════════════════

async def _morning_event_driven(bot, user_id: str, trigger: str = "wake") -> bool:
    """事件驱动早安（真人化 P1-4）。

    trigger 类型：
    - "wake": schedule sleeping→waking 后 5-30 分钟触发
    - "user_morning": 用户先发早安 → 被动回复
    - "first_today": 对方今天第一条消息 → 顺带说早

    Returns:
        True 如果发送了早安
    """
    session_id = f"private_{user_id}"

    # 今天已发过，跳过
    if await has_proactive_today(str(user_id), "morning") or \
       await has_proactive_today(str(user_id), "morning_triggered"):
        return False

    # 获取虚拟时间（而非 datetime.now()）
    try:
        from ..causal_context import get_cc
        cc = get_cc(session_id)
        now = cc.virtual_time
    except Exception:
        from datetime import datetime
        now = datetime.now()

    # 只在早晨窗口触发（6:00-12:00）
    if not (6 <= now.hour < 12):
        return False

    if trigger == "wake":
        # 检查 schedule 状态是否刚 waking
        try:
            from ..causal_context import get_cc_safe
            cc = get_cc_safe(session_id)
            if cc and cc.schedule_period != "waking":
                return False
        except Exception:
            pass
        # 随机延迟 5-30 分钟后触发（由调用方控制）
        import random as _random
        delay_minutes = _random.randint(5, 30)
        logger.info(
            f"[早安事件] wake触发 {user_id[:6]} 延迟{delay_minutes}min "
            f"虚拟时间={now.strftime('%H:%M')}"
        )

    elif trigger == "user_morning":
        # 用户先发了早安 → 被动回复
        logger.info(f"[早安事件] 被动回复 {user_id[:6]}")

    elif trigger == "first_today":
        # 今天第一条消息 → 自然带早安
        logger.info(f"[早安事件] 首条消息顺带 {user_id[:6]}")

    # 生成早安消息
    msg = await _generate_proactive_message("morning", str(user_id))
    await _send_proactive_message(bot, "private", str(user_id), msg, scene="morning")
    logger.info(f"[早安事件] 发送 {user_id[:6]}: {msg[:30]}...")
    return True


async def handle_user_morning(bot, user_id: str, raw_msg: str) -> bool:
    """处理用户主动早安（在 handler 中调用）。

    如果用户先发早安，bot 被动回复，不再主动发送早安。
    """
    from ..handler_helpers import detect_greeting_type
    greeting_type = detect_greeting_type(raw_msg, [])
    if greeting_type == "morning":
        return await _morning_event_driven(bot, user_id, trigger="user_morning")
    return False
