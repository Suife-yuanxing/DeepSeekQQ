"""通知 API — Task 1.10。

站内通知系统。
对齐前端 [通知.html] 的数据展示。

v2 修正：
  - 4 类型：system / msg / bot / update
  - 游标分页 + 按 type/unread 过滤
  - 单条/全部已读
"""
import time
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from pydantic import BaseModel

from .deps import get_current_user
from ..db_platform import get_notifications
from ..db_platform import get_unread_count
from ..db_platform import mark_notification_read
from ..db_platform import mark_all_notifications_read

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(
    type_: Optional[str] = Query(None, alias="type"),
    unread: Optional[bool] = Query(None),
    cursor: Optional[float] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(get_current_user),
):
    """获取通知列表，游标分页。

    支持按 type (system/msg/bot/update) 和 is_read 过滤。
    分组依赖前端按 created_at 做（今天/昨天/更早）。
    """
    notes = await get_notifications(
        user["id"], type_=type_, unread=unread, cursor=cursor, limit=limit,
    )
    return {
        "notifications": [_n_public(n) for n in notes],
        "has_more": len(notes) == limit,
        "next_cursor": notes[-1]["created_at"] if notes and len(notes) == limit else None,
    }


@router.get("/unread-count")
async def unread_count(user=Depends(get_current_user)):
    """未读通知数（红点用）。"""
    count = await get_unread_count(user["id"])
    return {"count": count}


@router.patch("/{notification_id}/read")
async def mark_read(notification_id: int, user=Depends(get_current_user)):
    """标记单条通知为已读。"""
    ok = await mark_notification_read(user["id"], notification_id)
    if not ok:
        raise HTTPException(status_code=404, detail={"code": "notification_not_found", "message": "通知不存在"})
    return {"ok": True}


@router.patch("/read-all")
async def mark_all_read(user=Depends(get_current_user)):
    """标记所有通知为已读。"""
    count = await mark_all_notifications_read(user["id"])
    return {"ok": True, "updated": count}


def _n_public(n: dict) -> dict:
    return {
        "id": n["id"],
        "type": n["type"],
        "title": n["title"],
        "body": n["body"],
        "is_read": bool(n["is_read"]),
        "related_id": n.get("related_id"),
        "created_at": n["created_at"],
    }
