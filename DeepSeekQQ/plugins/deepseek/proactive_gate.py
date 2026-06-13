"""P1-12: 主动消息统一门控。

每日上限 5 条 + 最小间隔 2h + 退订机制。
所有主动消息（早安/晚安/沉默检测/节日问候/热搜推送）都必须通过此门控。
"""
import asyncio
import logging
import time
from typing import Optional

from nonebot import logger

from .config import MY_QQ

# ============================================================
# 门控配置
# ============================================================

MAX_PROACTIVE_PER_DAY = 5       # 每日主动消息上限
MIN_PROACTIVE_INTERVAL = 7200   # 最小间隔（秒）= 2 小时
OPT_OUT_KEYWORDS = [            # 退订关键词
    "别发消息了", "别打扰我", "不要主动发", "关掉提醒",
    "不要早安", "不要晚安", "别找我", "别烦我",
    "退订", "unsubscribe", "stop",
]
OPT_IN_KEYWORDS = [             # 重新订阅关键词
    "可以发消息了", "恢复消息", "我想你了", "继续发吧",
    "订阅", "subscribe", "start",
]

# 退订状态缓存（session 内有效，重启后清理）
_opt_out_users: dict = {}  # user_id -> opt_out_at (timestamp)
_opt_out_lock = asyncio.Lock()

# 最后发送时间追踪
_last_send_time: dict = {}  # user_id -> last_send_timestamp
_send_lock = asyncio.Lock()


# ============================================================
# 退订/订阅管理
# ============================================================

async def check_opt_status(user_id: str) -> bool:
    """检查用户是否已退订主动消息。

    Returns:
        True = 已退订（不应发送）
        False = 未退订（可以发送）
    """
    async with _opt_out_lock:
        if user_id in _opt_out_users:
            return True
    return False


async def process_opt_message(user_id: str, raw_msg: str) -> Optional[str]:
    """处理用户消息中的退订/订阅意图。

    Returns:
        "opted_out" — 用户已退订
        "opted_in" — 用户已重新订阅
        None — 无相关意图
    """
    async with _opt_out_lock:
        # 检查退订
        for kw in OPT_OUT_KEYWORDS:
            if kw in raw_msg:
                _opt_out_users[user_id] = time.time()
                logger.info(f"[主动消息] 用户 {user_id[:6]} 退订主动消息")
                return "opted_out"

        # 检查重新订阅
        if user_id in _opt_out_users:
            for kw in OPT_IN_KEYWORDS:
                if kw in raw_msg:
                    del _opt_out_users[user_id]
                    logger.info(f"[主动消息] 用户 {user_id[:6]} 重新订阅主动消息")
                    return "opted_in"

    return None


# ============================================================
# 统一门控
# ============================================================

async def proactive_gate(user_id: str, scene: str = "") -> tuple[bool, str]:
    """主动消息统一门控。

    所有主动消息发送前必须通过此门控。

    Args:
        user_id: 目标用户 ID
        scene: 场景名（morning/night/silence/holiday/hot_topic/sleep_nag）

    Returns:
        (allowed, reason)
        - allowed=True: 可以发送
        - allowed=False: 拒绝发送，reason 说明原因
    """
    now = time.time()

    # 1. 退订检查
    if await check_opt_status(user_id):
        return False, "opted_out"

    # 2. 每日上限检查
    try:
        from .database import get_today_proactive_count
        count = await get_today_proactive_count(user_id)
        if count >= MAX_PROACTIVE_PER_DAY:
            return False, f"daily_limit({count}/{MAX_PROACTIVE_PER_DAY})"
    except Exception as e:
        logger.debug(f"[主动门控] 每日计数查询失败: {e}")
        # 查询失败时不阻塞（fail-open）

    # 3. 最小间隔检查（2h）
    async with _send_lock:
        last_ts = _last_send_time.get(user_id, 0)
        elapsed = now - last_ts
        if elapsed < MIN_PROACTIVE_INTERVAL:
            minutes_remaining = (MIN_PROACTIVE_INTERVAL - elapsed) / 60
            return False, f"cooldown({int(minutes_remaining)}min remaining)"

    # 4. 活跃检查（用户最近15分钟有消息就不打扰）
    try:
        from .database import has_recent_message
        if await has_recent_message(user_id, minutes=15):
            return False, "user_active"
    except Exception as e:
        logger.debug(f"[主动门控] 活跃检查失败: {e}")

    return True, "ok"


async def record_proactive_sent(user_id: str, scene: str, content: str):
    """记录主动消息已发送，更新间隔追踪。"""
    now = time.time()
    async with _send_lock:
        _last_send_time[user_id] = now

    try:
        from .database import log_proactive
        await log_proactive(user_id, "private", content, scene=scene)
    except Exception as e:
        logger.debug(f"[主动门控] 记录失败: {e}")


def get_gate_stats() -> dict:
    """获取门控统计信息。"""
    return {
        "opt_out_count": len(_opt_out_users),
        "tracked_users": len(_last_send_time),
        "config": {
            "max_per_day": MAX_PROACTIVE_PER_DAY,
            "min_interval_hours": MIN_PROACTIVE_INTERVAL / 3600,
        },
    }
