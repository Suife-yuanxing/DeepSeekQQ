"""P1-12: 主动消息统一门控。

每日上限 5 条 + 最小间隔 2h + 退订机制。
所有主动消息（早安/晚安/沉默检测/节日问候/热搜推送）都必须通过此门控。

HF-1 (2026-06-14): opt-out 从内存字典迁移到 DB 持久化（重启保留）。
HF-2 (2026-06-14): 退订关键词从宽泛子串匹配改为精确命令匹配。
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

# HF-2: 精确退订/订阅命令（完整匹配，不再使用子串匹配避免误触发）
OPT_OUT_COMMANDS = {
    "退订", "停止推送", "关闭主动消息", "/optout",
    "别发消息了", "不要再发了",
}
OPT_IN_COMMANDS = {
    "订阅", "开启推送", "恢复主动消息", "/optin",
    "可以发消息了", "继续发吧",
}

# 优雅退避参数
MAX_IGNORE_BEFORE_BACKOFF = 2    # 连续忽略 2 次开始退避
BACKOFF_MULTIPLIER = 1.5         # 间隔乘数
MAX_IGNORE_BEFORE_SUSPEND = 5    # 连续 5 次忽略 → 暂停 7 天
SUSPEND_DAYS = 7

# 最后发送时间追踪（冷却可接受内存存储，重要性低于退订）
_last_send_time: dict = {}  # user_id -> last_send_timestamp
_send_lock = asyncio.Lock()


# ============================================================
# 退订/订阅管理（DB 持久化）
# ============================================================

async def _get_db():
    """获取数据库连接（延迟导入避免循环依赖）。"""
    from .db_core import get_db
    return await get_db()


async def is_opted_out(user_id: str) -> bool:
    """从 DB 读取退订状态（替代原内存 _opt_out_users 字典）。

    Returns:
        True = 已退订（不应发送）
        False = 未退订（可以发送）
    """
    try:
        db = await _get_db()
        async with db.execute(
            "SELECT 1 FROM proactive_opt_out WHERE user_id = ?", (str(user_id),)
        ) as cur:
            row = await cur.fetchone()
            return row is not None
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"[主动门控] opt-out 查询失败: {e}")
        return False  # fail-open：查询失败不阻塞


async def set_opt_out(user_id: str, reason: str = ""):
    """持久化退订状态。"""
    try:
        db = await _get_db()
        await db.execute(
            "INSERT OR REPLACE INTO proactive_opt_out (user_id, opted_out_at, reason) VALUES (?, ?, ?)",
            (str(user_id), time.time(), reason)
        )
        await db.commit()
        logger.info(f"[主动消息] 用户 {user_id[:6]} 退订主动消息 (原因: {reason})")
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f"[主动门控] opt-out 持久化失败: {e}")


async def clear_opt_out(user_id: str):
    """用户重新订阅——精确命令匹配触发。"""
    try:
        db = await _get_db()
        await db.execute(
            "DELETE FROM proactive_opt_out WHERE user_id = ?", (str(user_id),)
        )
        await db.commit()
        logger.info(f"[主动消息] 用户 {user_id[:6]} 重新订阅主动消息")
    except (OSError, ValueError, TypeError) as e:
        logger.warning(f"[主动门控] opt-in 持久化失败: {e}")


async def get_opt_out_count() -> int:
    """获取当前退订用户数。"""
    try:
        db = await _get_db()
        async with db.execute("SELECT COUNT(*) as c FROM proactive_opt_out") as cur:
            row = await cur.fetchone()
            return row["c"] if row else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0


async def process_opt_message(user_id: str, raw_msg: str) -> Optional[str]:
    """处理用户消息中的退订/订阅意图（精确命令匹配）。

    HF-2: 从宽泛子串匹配改为精确命令匹配。
    只有消息内容（去除首尾空白）完全匹配退订/订阅命令才触发。

    Returns:
        "opted_out" — 用户已退订
        "opted_in" — 用户已重新订阅
        None — 无相关意图
    """
    msg = raw_msg.strip()

    # 精确匹配退订命令
    if msg in OPT_OUT_COMMANDS:
        await set_opt_out(user_id, reason=msg)
        return "opted_out"

    # 精确匹配订阅命令
    if msg in OPT_IN_COMMANDS:
        await clear_opt_out(user_id)
        return "opted_in"

    return None


# ============================================================
# 优雅退避（HF-1 新增）
# ============================================================

async def record_proactive_ignored(user_id: str):
    """记录用户忽略了主动消息（未回复），用于退避计算。"""
    try:
        from .db_core import get_db
        db = await get_db()
        now = time.time()

        # 获取当前状态
        async with db.execute(
            "SELECT ignore_count, backoff_until FROM proactive_backoff WHERE user_id = ?",
            (str(user_id),)
        ) as cur:
            row = await cur.fetchone()

        if row:
            new_count = row["ignore_count"] + 1
        else:
            new_count = 1

        # 计算退避
        backoff_until = 0
        if new_count >= MAX_IGNORE_BEFORE_SUSPEND:
            backoff_until = now + SUSPEND_DAYS * 86400
        elif new_count >= MAX_IGNORE_BEFORE_BACKOFF:
            base_interval = MIN_PROACTIVE_INTERVAL
            multiplier = BACKOFF_MULTIPLIER ** (new_count - MAX_IGNORE_BEFORE_BACKOFF + 1)
            backoff_until = now + base_interval * multiplier

        await db.execute(
            """INSERT OR REPLACE INTO proactive_backoff
               (user_id, ignore_count, backoff_until, last_ignored_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (str(user_id), new_count, backoff_until, now, now)
        )
        await db.commit()

        if backoff_until > 0:
            logger.info(
                f"[主动消息] 用户 {user_id[:6]} 连续忽略 {new_count} 次，"
                f"退避至 {time.strftime('%m-%d %H:%M', time.localtime(backoff_until))}"
            )
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"[主动门控] 退避记录失败: {e}")


async def reset_backoff(user_id: str):
    """用户主动发消息时重置退避计数。"""
    try:
        from .db_core import get_db
        db = await get_db()
        await db.execute(
            "UPDATE proactive_backoff SET ignore_count = 0, backoff_until = 0, updated_at = ? WHERE user_id = ?",
            (time.time(), str(user_id))
        )
        await db.commit()
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as e:
        logger.debug(f"[主动门控] 退避重置失败: {e}")


async def check_backoff(user_id: str) -> tuple[bool, str]:
    """检查用户是否处于退避期。

    Returns:
        (is_in_backoff, reason)
    """
    try:
        from .db_core import get_db
        db = await get_db()
        now = time.time()
        async with db.execute(
            "SELECT ignore_count, backoff_until FROM proactive_backoff WHERE user_id = ?",
            (str(user_id),)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return False, ""
            if row["backoff_until"] > now:
                remaining = (row["backoff_until"] - now) / 3600
                if row["ignore_count"] >= MAX_IGNORE_BEFORE_SUSPEND:
                    return True, f"suspended({remaining:.0f}h)"
                else:
                    return True, f"backoff({remaining:.0f}h)"
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        pass
    return False, ""


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

    # 1. 退订检查（DB 持久化）
    if await is_opted_out(user_id):
        return False, "opted_out"

    # 2. 退避检查（HF-1 新增）
    in_backoff, reason = await check_backoff(user_id)
    if in_backoff:
        return False, reason

    # 3. 每日上限检查
    try:
        from .database import get_today_proactive_count
        count = await get_today_proactive_count(user_id)
        if count >= MAX_PROACTIVE_PER_DAY:
            return False, f"daily_limit({count}/{MAX_PROACTIVE_PER_DAY})"
    except (ImportError, AttributeError, OSError, ValueError, TypeError) as e:
        logger.debug(f"[主动门控] 每日计数查询失败: {e}")
        # 查询失败时不阻塞（fail-open）

    # 4. 最小间隔检查（2h）
    async with _send_lock:
        last_ts = _last_send_time.get(user_id, 0)
        elapsed = now - last_ts
        if elapsed < MIN_PROACTIVE_INTERVAL:
            minutes_remaining = (MIN_PROACTIVE_INTERVAL - elapsed) / 60
            return False, f"cooldown({int(minutes_remaining)}min remaining)"

    # 5. 活跃检查（用户最近15分钟有消息就不打扰）
    try:
        from .database import has_recent_message
        if await has_recent_message(user_id, minutes=15):
            return False, "user_active"
    except (ImportError, AttributeError, OSError, ValueError, TypeError) as e:
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
    except (ImportError, AttributeError, OSError, ValueError, TypeError) as e:
        logger.debug(f"[主动门控] 记录失败: {e}")


async def get_gate_stats() -> dict:
    """获取门控统计信息。"""
    return {
        "opt_out_count": await get_opt_out_count(),
        "tracked_users": len(_last_send_time),
        "config": {
            "max_per_day": MAX_PROACTIVE_PER_DAY,
            "min_interval_hours": MIN_PROACTIVE_INTERVAL / 3600,
        },
    }
