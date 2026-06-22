"""额度管理 API — Task 1.8。

日限额 + 队列状态。
对齐前端 [首页仪表盘.html] + [API Key管理.html] 的额度展示。

v2 修正：
  - 依赖 Phase 0.6 llm_queue.get_queue_stats()
  - 区分平台 Key 额度 vs 用户自带 Key
"""
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import Query

from .deps import get_current_user
from ..db_platform import get_daily_quota
from ..llm_queue import get_queue_stats

router = APIRouter(prefix="/api/v1", tags=["quota"])


@router.get("/quota")
async def get_quota(bot_id: Optional[int] = Query(None), user=Depends(get_current_user)):
    """获取额度信息：日使用量/限额 + 队列深度。"""
    daily = await get_daily_quota(user["id"])
    qs = get_queue_stats()
    return {
        "daily_used": daily["daily_used"],
        "daily_limit": daily["daily_limit"],
        "daily_remaining": max(0, daily["daily_limit"] - daily["daily_used"]),
        "queue_depth": qs["queued"],
        "queue_active": qs["active"],
        "queue_wait_users": max(0, qs["queued"]),
        "tier": daily["tier"],
        "upgrade_hint": "当前为平台额度，日限额 50 条。使用自己的 API Key 可获得无限消息。" if daily["tier"] == "platform" else "",
    }


@router.get("/quota/status")
async def get_quota_status(user=Depends(get_current_user)):
    """获取账户额度状态摘要。"""
    daily = await get_daily_quota(user["id"])
    return {
        "tier": daily["tier"],
        "daily_remaining": max(0, daily["daily_limit"] - daily["daily_used"]),
        "daily_limit": daily["daily_limit"],
        "upgrade_hint": "当前使用平台免费额度。建议自带 API Key 获得无限消息。" if daily["tier"] == "platform" else "",
    }
