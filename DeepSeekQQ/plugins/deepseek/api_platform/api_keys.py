"""API Key 管理 API — Task 1.3。

对齐前端 [API Key管理.html] 的 6 个端点。
v2 审计修正落地：
  - H5: 所有 /{id} 端点强制 ownership 校验（key.user_id == JWT.user_id，不符 403 apikey_not_owned）
  - 前端永远不返回完整 key，只显示 key_suffix（后 4 位）
  - AES-256-GCM 加密存储（kms.py），主密钥从环境变量
"""
import json
import time
from typing import Optional

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Query
from fastapi import status
from pydantic import BaseModel
from pydantic import Field

from .deps import get_current_user
from .kms import encrypt_api_key
from .kms import key_suffix
from ..db_core import get_db
from ..db_platform import create_api_key
from ..db_platform import get_api_key_usage_summary
from ..db_platform import get_user_api_keys
from ..db_platform import revoke_api_key

router = APIRouter(prefix="/api/v1/api-keys", tags=["api-keys"])

# provider 白名单（对齐 config.py 已有 provider 配置）
VALID_PROVIDERS = {
    "deepseek", "kimi", "minimax", "mimo", "mimo_stt",
    "glm", "qwen_vl", "tavily", "agnes", "baidu_tts", "volcano_tts",
}

# 合法 scope（对齐前端 API Key管理.html 的 perm-tag）
VALID_SCOPES = {"chat", "image", "voice", "memory", "search", "vision"}


# ============================================================
# Pydantic 模型
# ============================================================

class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=40, description="Key 备注名，如「生产环境」")
    provider: str = Field(..., description="提供者，必须在白名单内")
    key_value: str = Field(..., min_length=8, max_length=200, description="API Key 明文，仅创建时传输")
    scopes: Optional[list[str]] = Field(None, description="权限范围，如 [chat, image]")
    # expires_at: 可选，表结构暂无该列，预留字段不存储，后续 ALTER TABLE 补


# ============================================================
# 辅助
# ============================================================

async def _require_api_key_owner(key_id: int, user: dict) -> dict:
    """H5: 校验 API Key 属于当前用户，返回 key dict，否则 403。

    注意：get_user_api_keys 只返回脱敏字段（无 encrypted_key），
    这里单独查 user_id 做归属校验。
    """
    db = await get_db()
    async with db.execute(
        "SELECT id, user_id, provider, key_suffix, name, scopes, is_active, created_at, last_used "
        "FROM user_api_keys WHERE id = ?",
        (key_id,),
    ) as cur:
        row = await cur.fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "apikey_not_found", "message": "API Key 不存在"},
        )
    key = dict(row)
    if key["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "apikey_not_owned", "message": "无权操作该 API Key"},
        )
    return key


def _key_public(key: dict) -> dict:
    """脱敏 API Key 对象（前端可见字段）。"""
    try:
        scopes = json.loads(key["scopes"]) if key.get("scopes") else []
    except (json.JSONDecodeError, TypeError):
        scopes = []
    return {
        "id": key["id"],
        "name": key.get("name", ""),
        "provider": key["provider"],
        "key_suffix": key["key_suffix"],
        "scopes": scopes,
        "status": "active" if key.get("is_active", 1) else "revoked",
        "is_active": bool(key.get("is_active", 1)),
        "created_at": key.get("created_at", 0),
        "last_used": key.get("last_used", 0),
    }


# ============================================================
# 端点
# ============================================================

@router.get("")
async def list_api_keys(user=Depends(get_current_user)):
    """列出当前用户所有 API Key（只返回 key_suffix，绝不下发完整 key）。"""
    keys = await get_user_api_keys(user["id"])
    return {"keys": [_key_public(k) for k in keys], "count": len(keys)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_api_key_endpoint(req: ApiKeyCreate, user=Depends(get_current_user)):
    """新建 API Key。明文经 AES-256-GCM 加密后存 encrypted_key，只返回 key_suffix。"""
    # provider 白名单校验
    if req.provider not in VALID_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_provider",
                    "message": f"provider 必须是 {sorted(VALID_PROVIDERS)} 之一"},
        )
    # scope 白名单校验
    if req.scopes:
        invalid = [s for s in req.scopes if s not in VALID_SCOPES]
        if invalid:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "invalid_scope",
                        "message": f"非法 scope: {invalid}，合法: {sorted(VALID_SCOPES)}"},
            )
    encrypted = encrypt_api_key(req.key_value)
    suffix = key_suffix(req.key_value)
    kid = await create_api_key(
        user_id=user["id"],
        provider=req.provider,
        encrypted_key=encrypted,
        key_suffix=suffix,
        name=req.name,
        scopes=req.scopes,
    )
    return {"id": kid, "key_suffix": suffix, "name": req.name, "provider": req.provider}


@router.post("/{key_id}/revoke")
async def revoke_api_key_endpoint(key_id: int, user=Depends(get_current_user)):
    """吊销 API Key（H5 ownership 校验）。软删除：is_active 置 0。"""
    await _require_api_key_owner(key_id, user)
    ok = await revoke_api_key(user["id"], key_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "apikey_not_found", "message": "API Key 不存在或已吊销"},
        )
    return {"ok": True, "id": key_id, "status": "revoked"}


@router.get("/usage-summary")
async def usage_summary(user=Depends(get_current_user)):
    """API Key 用量概览（对齐前端 usage-summary 三格：总调用/活跃 Key/成功率）。

    total_calls / success_rate 依赖 token_tracker 按 user_id 聚合，
    token_tracker 当前无 user_id 字段（Task 1.11 v2 已标注需改），
    此处先返回占位 0，真实聚合在 Task 1.16 补。
    """
    summary = await get_api_key_usage_summary(user["id"])
    return {
        "total_calls": 0,  # TODO: Task 1.16 接 token_tracker 按 user_id 聚合
        "active_keys": summary["active_keys"],
        "total_keys": summary["total_keys"],
        "providers": summary["providers"],
        "success_rate": 0.0,  # TODO: 同上
    }


@router.get("/{key_id}/usage")
async def key_usage(
    key_id: int,
    user=Depends(get_current_user),
    range_param: str = Query("7d", pattern="^(7d|30d)$", alias="range"),
):
    """单个 Key 的每日调用量（对齐前端「用量明细」7 天柱图）。

    TODO: token_tracker 当前无 user_id/key_id 维度，先返回空数组占位，
    Task 1.16 落地真实聚合时补 [(date, calls)]。
    """
    await _require_api_key_owner(key_id, user)
    # 占位：返回最近 N 天的零值骨架，前端可正常渲染空柱图
    days = 7 if range_param == "7d" else 30
    today = int(time.time() // 86400 * 86400)
    skeleton = [
        {"date": time.strftime("%Y-%m-%d", time.localtime(today - (days - 1 - i) * 86400)),
         "calls": 0}
        for i in range(days)
    ]
    return {"key_id": key_id, "range": range_param, "daily": skeleton}


@router.get("/{key_id}/endpoints")
async def key_endpoints(
    key_id: int,
    user=Depends(get_current_user),
    range_param: str = Query("30d", pattern="^(7d|30d|90d)$", alias="range"),
):
    """单个 Key 的端点调用占比（对齐前端「端点分布」进度条）。

    TODO: 同上，token_tracker 无端点维度，先返回空数组占位。
    """
    await _require_api_key_owner(key_id, user)
    return {"key_id": key_id, "range": range_param, "endpoints": []}
