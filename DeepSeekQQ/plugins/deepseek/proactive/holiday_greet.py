"""节日问候模块。"""
from datetime import datetime

from ..config import PROACTIVE_CONFIG
from .shared import _generate_proactive_message, _send_proactive_message


async def _holiday_greeting(bot):
    """节日祝福：匹配当天日期到配置的节日列表。"""
    cfg = PROACTIVE_CONFIG["holiday_greeting"]
    if not cfg["enabled"]:
        return
    today = datetime.now().strftime("%m-%d")
    if today in cfg["holidays"]:
        holiday_name = cfg["holidays"][today]  # fallback
        target_users = cfg["target_users"]() if callable(cfg["target_users"]) else cfg["target_users"]
        for uid in target_users:
            msg = await _generate_proactive_message("holiday", str(uid))
            await _send_proactive_message(bot, "private", str(uid), msg, scene="holiday")
        for gid in cfg["target_groups"]:
            msg = await _generate_proactive_message("holiday")
            await _send_proactive_message(bot, "group", str(gid), msg, scene="holiday")
