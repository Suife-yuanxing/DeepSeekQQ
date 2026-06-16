"""凌晨催睡 + 承诺检查模块。"""
from datetime import datetime

from nonebot import logger

from ..config import MY_QQ
from ..config import PROACTIVE_CONFIG
from ..database import get_today_proactive_count_by_scene
from ..database import has_recent_message
from .shared import (
    _generate_proactive_message,
    _get_proactive_targets,
    _send_proactive_message,
)


async def _sleep_nag(bot):
    """凌晨催睡：00:00-02:00 每 30 分钟检查，用户还在聊天就催。

    多用户支持：自动发现活跃会话中的目标用户。
    """
    hour = datetime.now().hour
    if not (0 <= hour < 2):
        return
    cfg = PROACTIVE_CONFIG.get("sleep_nag", {})
    if not cfg.get("enabled", True):
        return
    max_nags = cfg.get("max_nags_per_night", 2)
    today = datetime.now().strftime("%Y-%m-%d")

    # 多用户：从活跃会话自动发现目标
    target_users = await _get_proactive_targets()
    if MY_QQ and str(MY_QQ) not in target_users:
        target_users.insert(0, str(MY_QQ))

    for uid in target_users:
        nag_count = await get_today_proactive_count_by_scene(str(uid), "sleep_nag", today)
        if nag_count >= max_nags:
            continue
        session_id = f"private_{uid}"
        if not await has_recent_message(session_id, minutes=30):
            continue
        msg = await _generate_proactive_message("sleep_nag", str(uid))
        await _send_proactive_message(bot, "private", str(uid), msg, scene="sleep_nag")


async def _check_promises(bot):
    """检查到期承诺并推送兑现/道歉消息。"""
    try:
        from ..promise_tracker import (
            get_due_promises, get_forgotten_to_apologize,
            get_fulfill_prefix, get_forgotten_apology,
            mark_fulfilled, mark_apologized,
        )
    except Exception:
        return

    # 1. 兑现到期承诺
    due = await get_due_promises()
    for p in due:
        try:
            uid = p["user_id"]
            prefix = get_fulfill_prefix(p["promise_text"])
            msg = f"{prefix}"
            await _send_proactive_message(bot, "private", uid, msg, scene="promise_fulfill")
            await mark_fulfilled(p["id"])
            logger.info(f"[承诺追踪] 兑现: {p['promise_text'][:30]} → {uid}")
        except Exception as e:
            logger.error(f"[承诺追踪] 兑现失败: {e}")

    # 2. 遗忘道歉（在due_at后1-3天内）
    forgotten = await get_forgotten_to_apologize()
    for p in forgotten:
        try:
            uid = p["user_id"]
            msg = get_forgotten_apology(p["promise_text"])
            await _send_proactive_message(bot, "private", uid, msg, scene="promise_apology")
            await mark_apologized(p["id"])
            logger.info(f"[承诺追踪] 遗忘道歉: {p['promise_text'][:30]} → {uid}")
        except Exception as e:
            logger.error(f"[承诺追踪] 道歉失败: {e}")
