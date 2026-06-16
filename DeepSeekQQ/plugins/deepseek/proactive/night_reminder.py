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
