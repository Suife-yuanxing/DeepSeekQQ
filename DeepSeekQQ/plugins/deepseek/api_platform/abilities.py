"""能力配置 API — Task 1.7。

Bot 的能力开关 + 提供者选择。
对齐前端 [API Key管理.html] + [Bot设置.html] 的能力配置部分。

v2 修正：
  - H5: 所有 /{id} 端点强制 ownership 校验
  - 能力配置与 Task 1.3 KMS 解耦（只配开关，Key 管理在 1.3）
"""
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

from .deps import get_current_user
from .deps import require_bot_owner
from ..db_platform import get_bot_abilities
from ..db_platform import update_bot_abilities

router = APIRouter(prefix="/api/v1/bots", tags=["abilities"])

# 有效 provider 枚举
VALID_PROVIDERS = {
    "llm": ("platform", "user_key", "off"),
    "vision": ("platform_glm", "user_qwen", "off"),
    "tts": ("off", "platform_baidu", "user_volcano", "user_mimo"),
    "stt": ("off", "platform_baidu", "user_mimo"),
    "image_gen": ("off", "user_key"),
    "search": (True, False),
    "weather": (True, False),
    "hot_topics": (True, False),
}


class AbilitiesPut(BaseModel):
    llm: Optional[dict] = None
    vision: Optional[dict] = None
    tts: Optional[dict] = None
    stt: Optional[dict] = None
    image_gen: Optional[dict] = None
    search: Optional[dict] = None
    weather: Optional[dict] = None
    hot_topics: Optional[dict] = None


@router.get("/{bot_id}/abilities")
async def get_abilities(bot_id: int, user=Depends(get_current_user)):
    """H5: ownership 校验。获取 Bot 能力配置。"""
    await require_bot_owner(bot_id, user)
    return await get_bot_abilities(bot_id)


@router.put("/{bot_id}/abilities")
async def put_abilities(bot_id: int, req: AbilitiesPut, user=Depends(get_current_user)):
    """H5: ownership 校验。更新 Bot 能力配置（部分更新）。"""
    await require_bot_owner(bot_id, user)
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if fields:
        merged = await update_bot_abilities(bot_id, fields)
        return merged
    return await get_bot_abilities(bot_id)
