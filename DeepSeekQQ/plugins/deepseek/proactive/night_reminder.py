"""晚安提醒模块：多时段检查 + 道别去重。"""
from nonebot import logger

from ..config import MY_QQ
from ..config import PROACTIVE_CONFIG
from ..database import has_proactive_today
from ..database import has_recent_message
from .shared import (
    _generate_proactive_message,
    _get_proactive_targets,
    _send_proactive_message,
)


async def _night_greeting(bot):
    """智能晚安：多时段检查 + 道别去重。

    仅对最近30分钟有消息的用户发送，避免对已沉默用户发晚安。
    增加道别去重：如果用户已通过感知式道别说晚安，不再重复发送。
    """
    cfg = PROACTIVE_CONFIG["night_greeting"]
    if not cfg["enabled"]:
        return

    # 多用户：从活跃会话自动发现目标
    target_users = await _get_proactive_targets()
    if MY_QQ and str(MY_QQ) not in target_users:
        target_users.insert(0, str(MY_QQ))

    for uid in target_users:
        session_id = f"private_{uid}"
        if not await has_recent_message(session_id, minutes=30):
            continue

        # 道别去重：今天已发过晚安或已感知式道别 → 跳过
        if await has_proactive_today(str(uid), "night") or \
           await has_proactive_today(str(uid), "farewell"):
            logger.debug(f"[晚安] 跳过 {str(uid)[:6]}: 今日已道别")
            continue

        msg = await _generate_proactive_message("night", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg, scene="night")

    for gid in cfg["target_groups"]:
        session_id = f"group_{gid}"
        if not await has_recent_message(session_id, minutes=30):
            continue
        if await has_proactive_today(str(gid), "night"):
            continue
        msg = await _generate_proactive_message("night")
        await _send_proactive_message(bot, "group", str(gid), msg, scene="night")


# ═══════════════════════════════════════════════════════════════
# 事件驱动晚安 — 真人化 P1-4
# ═══════════════════════════════════════════════════════════════

async def _night_event_driven(bot, user_id: str) -> bool:
    """事件驱动晚安（真人化 P1-4）。

    触发条件（AND）：
    1. CausalContext.is_ending=True（对话自然收尾）
    2. fatigue_level >= 2（用户疲劳）
    3. 虚拟时间 >= 23:00
    4. 对话已自然结束 → 不重复发晚安（audit-1-4 修复）

    Returns:
        True if night greeting was sent
    """
    session_id = f"private_{user_id}"

    # 今天已道别，跳过
    if await has_proactive_today(str(user_id), "night") or \
       await has_proactive_today(str(user_id), "farewell"):
        return False

    # 获取 CausalContext 状态
    try:
        from ..causal_context import get_cc_safe
        cc = get_cc_safe(session_id)
        if not cc:
            return False

        # 虚拟时间检查
        now = cc.virtual_time
        if now.hour < 23:
            return False

        # 对话疲劳检查
        if cc.fatigue_level < 2:
            return False

        # 对话收尾检查
        if not cc.is_ending:
            return False

        logger.info(
            f"[晚安事件] 事件触发 {user_id[:6]} "
            f"fatigue={cc.fatigue_level} ending={cc.is_ending} "
            f"time={now.strftime('%H:%M')}"
        )
    except Exception as e:
        logger.debug(f"[晚安事件] CausalContext 不可用: {e}")
        return False

    # 生成晚安消息
    msg = await _generate_proactive_message("night", str(user_id))
    await _send_proactive_message(bot, "private", str(user_id), msg, scene="night")
    logger.info(f"[晚安事件] 发送 {user_id[:6]}: {msg[:30]}...")
    return True


async def check_night_event(bot, user_id: str, session_id: str) -> bool:
    """检查是否应该触发事件驱动晚安（在 handler 中每个回复后调用）。

    真人化 P1-4 + audit-1-4 修复：
    - 由对话收尾触发，而非定时器
    - 对话已自然结束 → 不再发晚安
    """
    try:
        from ..causal_context import get_cc_safe
        cc = get_cc_safe(session_id)
        if not cc:
            return False

        # 条件：疲劳>=2 + 收尾 + >=23:00
        if cc.fatigue_level >= 2 and cc.is_ending and cc.virtual_time.hour >= 23:
            return await _night_event_driven(bot, user_id)
    except Exception:
        pass
    return False
