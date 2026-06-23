"""Bot CRUD 端点 — Task 1.4 精简版。

v2 修正：
  - H5: 所有 /{id} 端点强制 ownership 校验（bot.user_id == JWT.user_id）
  - 滑块 6 维字段映射（前端 Bot设置.html）
  - 敏感词：P0 精简版暂不做（Task 1.15 完整版补）
"""
import json
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import status
from pydantic import BaseModel
from pydantic import Field

from .deps import get_current_user
from .deps import require_bot_owner
from ..db_platform import clear_bot_memory
from ..db_platform import create_bot
from ..db_platform import delete_bot
from ..db_platform import get_bot
from ..db_platform import get_bots_by_user
from ..db_platform import update_bot

router = APIRouter(prefix="/api/v1/bots", tags=["bots"])

# 6 种人格模板（对齐前端 Bot创建向导.html）
PERSONALITIES = {
    "tsundere": "傲娇",
    "gentle": "温柔",
    "sarcastic": "毒舌",
    "energetic": "元气",
    "emotionless": "三无",
    "sly": "腹黑",
}


class BotCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    personality: str = Field("gentle", pattern="^(tsundere|gentle|sarcastic|energetic|emotionless|sly)$")
    persona_description: Optional[str] = Field(None, max_length=500)
    traits: Optional[list[str]] = None
    catchphrase: Optional[str] = Field(None, max_length=100)
    age: Optional[int] = Field(None, ge=1, le=999)
    speech_style: Optional[str] = None
    backstory: Optional[str] = Field(None, max_length=500)
    special_rules: Optional[str] = Field(None, max_length=500)
    avatar_template: Optional[str] = None


class BotUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=30)
    personality: Optional[str] = Field(None, pattern="^(tsundere|gentle|sarcastic|energetic|emotionless|sly)$")
    # 滑块 6 维（前端 Bot设置.html）
    style_score: Optional[int] = Field(None, ge=0, le=10)        # 0=毒舌, 10=温柔
    talkativeness: Optional[int] = Field(None, ge=0, le=10)      # 0=话少, 10=话多
    formality: Optional[int] = Field(None, ge=0, le=10)          # 0=随意, 10=正式
    initiative: Optional[int] = Field(None, ge=0, le=10)         # 0=被动, 10=主动
    emotion_intensity: Optional[int] = Field(None, ge=10, le=100)
    reply_length: Optional[int] = Field(None, ge=0, le=4)
    # 称呼偏好
    call_preference: Optional[str] = Field(None, pattern="^(master|brother|sister|name|custom)$")
    custom_call: Optional[str] = Field(None, max_length=20)
    avatar_template: Optional[str] = None


@router.get("")
async def list_bots(user=Depends(get_current_user)):
    """列出当前用户的所有 Bot。H5: 自动按 user_id 过滤。"""
    bots = await get_bots_by_user(user["id"])
    return {"bots": [_bot_public(b) for b in bots], "count": len(bots)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_bot_endpoint(req: BotCreate, user=Depends(get_current_user)):
    """创建 Bot。"""
    persona = {
        "description": req.persona_description,
        "traits": req.traits or [],
        "catchphrase": req.catchphrase,
        "age": req.age,
        "speech_style": req.speech_style,
        "backstory": req.backstory,
        "special_rules": req.special_rules,
    }
    bot_id = await create_bot(
        user_id=user["id"],
        bot_name=req.name,
        personality=req.personality,
        persona_json=persona,
        avatar_template=req.avatar_template or "",
    )
    bot = await get_bot(bot_id)
    return _bot_public(bot)


@router.get("/{bot_id}")
async def get_bot_endpoint(bot_id: int, user=Depends(get_current_user)):
    """H5: ownership 校验。"""
    bot = await require_bot_owner(bot_id, user)
    return _bot_public(bot)


@router.put("/{bot_id}")
async def update_bot_endpoint(bot_id: int, req: BotUpdate, user=Depends(get_current_user)):
    """H5: ownership 校验。滑块 6 维存入 persona_json。"""
    bot = await require_bot_owner(bot_id, user)
    fields: dict = {}
    if req.name is not None:
        fields["bot_name"] = req.name
    if req.personality is not None:
        fields["personality"] = req.personality
    if req.avatar_template is not None:
        fields["avatar_template"] = req.avatar_template
    # 滑块/称呼偏好合并到 persona_json
    persona = json.loads(bot["persona_json"]) if bot["persona_json"] else {}
    slider_fields = (
        "style_score", "talkativeness", "formality", "initiative",
        "emotion_intensity", "reply_length", "call_preference", "custom_call",
    )
    persona_updated = False
    for f in slider_fields:
        v = getattr(req, f)
        if v is not None:
            persona[f] = v
            persona_updated = True
    if persona_updated:
        fields["persona_json"] = persona
    if fields:
        await update_bot(bot_id, fields)
    updated = await get_bot(bot_id)
    return _bot_public(updated)


@router.delete("/{bot_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot_endpoint(bot_id: int, user=Depends(get_current_user)):
    """H5: ownership 校验。级联删除消息。"""
    await require_bot_owner(bot_id, user)
    await delete_bot(bot_id)
    return None


@router.delete("/{bot_id}/memory")
async def clear_bot_memory_endpoint(bot_id: int, user=Depends(get_current_user)):
    """清除 Bot 聊天记忆。H5: ownership 校验。"""
    await require_bot_owner(bot_id, user)
    deleted = await clear_bot_memory(bot_id)
    return {"ok": True, "bot_id": bot_id, "deleted": deleted}


def _bot_public(bot: dict) -> dict:
    """脱敏 Bot 对象。"""
    persona = json.loads(bot["persona_json"]) if bot.get("persona_json") else {}
    return {
        "id": bot["id"],
        "name": bot["bot_name"],
        "personality": bot["personality"],
        "personality_label": PERSONALITIES.get(bot["personality"], bot["personality"]),
        "avatar_url": bot.get("avatar_url", ""),
        "avatar_template": bot.get("avatar_template", ""),
        "persona": persona,
        "is_active": bool(bot.get("is_active", 1)),
        "created_at": bot.get("created_at", 0),
        "updated_at": bot.get("updated_at", 0),
        # 兼容前端字段
        "today_count": 0,  # TODO: Task 1.9 仪表盘聚合补
        "level": 0,        # TODO: 对接 db_affection
        "status": "online",
    }
