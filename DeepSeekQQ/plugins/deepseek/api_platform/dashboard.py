"""仪表盘聚合 API — Task 1.9。

首页一个请求拿齐所有数据（避免 N+1）。
对齐前端 [首页仪表盘.html] 的数据展示。

v2 修正：
  - 聚合 5 个数据源：用户信息 + Bot 列表 + 今日统计 + 通道状态 + 队列
  - H5: Bot 数据自动按 user_id 过滤
"""
import time
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from .deps import get_current_user
from ..db_platform import get_bots_by_user
from ..db_platform import get_daily_quota
from ..db_platform import get_unread_count
from ..db_platform import get_channel_status
from ..db_platform import get_bot_graph_data
from ..llm_queue import get_queue_stats

router = APIRouter(prefix="/api/v1", tags=["dashboard"])


@router.get("/dashboard")
async def get_dashboard(user=Depends(get_current_user)):
    """首页仪表盘聚合端点。

    一个请求返回所有首页所需数据：
      - 用户信息
      - Bot 列表 + 各 Bot 状态
      - 今日消息数
      - 通道状态
      - 队列状态
      - 通知未读数
    """
    # 并行获取多个数据源
    import asyncio

    bots = await get_bots_by_user(user["id"])
    daily = await get_daily_quota(user["id"])
    qs = get_queue_stats()
    unread = await get_unread_count(user["id"])

    # 今日时段的问候语
    hour = time.localtime().tm_hour
    if 5 <= hour < 9:
        greeting = "早上好"
    elif 9 <= hour < 12:
        greeting = "上午好"
    elif 12 <= hour < 14:
        greeting = "中午好"
    elif 14 <= hour < 18:
        greeting = "下午好"
    elif 18 <= hour < 22:
        greeting = "晚上好"
    else:
        greeting = "夜深了"

    # 活跃度：取所有 Bot 今日消息数
    today_start = int(time.time() // 86400 * 86400)
    today_messages = daily["daily_used"] if daily["daily_used"] else 0

    return {
        "greeting": greeting,
        "user": {
            "id": user["id"],
            "nickname": user["nickname"],
            "avatar_url": user["avatar_url"],
            "is_admin": bool(user["is_admin"]),
        },
        "bots": [
            {
                "id": b["id"],
                "name": b["bot_name"],
                "personality": b["personality"],
                "avatar_url": b.get("avatar_url", ""),
                "status": "online" if b.get("is_active", 1) else "offline",
                "is_active": bool(b.get("is_active", 1)),
            }
            for b in bots
        ],
        "today": {
            "messages": today_messages,
            "daily_limit": daily["daily_limit"],
            "daily_remaining": max(0, daily["daily_limit"] - daily["daily_used"]),
            "tier": daily["tier"],
        },
        "notifications": {
            "unread_count": unread,
        },
        "queue": {
            "active": qs["active"],
            "queued": qs["queued"],
            "max_concurrent": qs["max_concurrent"],
        },
    }


@router.get("/dashboard/bot/{bot_id}")
async def get_bot_dashboard(bot_id: int, user=Depends(get_current_user)):
    """单个 Bot 的仪表盘详情。"""
    from .deps import require_bot_owner
    bot = await require_bot_owner(bot_id, user)

    # 并行获取
    import asyncio
    graph_data = await get_bot_graph_data(bot_id, days=7)
    channels = await get_channel_status(bot_id)

    # 今日消息数
    today_start = int(time.time() // 86400 * 86400)
    from ..db_core import get_db
    db = await get_db()
    async with db.execute(
        "SELECT COUNT(*) as cnt FROM chat_messages WHERE bot_id = ? AND created_at >= ? AND role = 'user'",
        (bot_id, today_start),
    ) as cur:
        row = await cur.fetchone()
    today_msgs = row["cnt"] if row else 0

    return {
        "bot": {
            "id": bot["id"],
            "name": bot["bot_name"],
            "personality": bot["personality"],
            "is_active": bool(bot.get("is_active", 1)),
        },
        "today_messages": today_msgs,
        "graph_7day": graph_data[-7:] if graph_data else [],
        "channels": [
            {
                "channel": c["channel"],
                "status": c["status"],
                "connected_at": c.get("connected_at", 0),
            }
            for c in channels
        ],
    }
