"""认证模块 — JWT 双 Token + bcrypt + 手机号 AES-256-GCM + SMS 验证码。

v2 审计修正落地：
  - S6: JWT 中间件独立于 ADMIN_API_KEY
  - S7: data_permissions 6 开关存 + 执行点在 Pipeline（本模块只存）
  - H6: OAuth state（P0 精简版无 OAuth，预留）
  - H7: refresh token 黑名单（revoked_tokens 表，logout 立即吊销无宽限期）
  - H8: refresh TTL 7d（非 30d）
  - 限流: SMS 1/min/IP + 5/h/phone（P0 精简版用内存计数，生产换 Redis）

P0 精简版取舍：
  - 无 OAuth（只手机号验证码）
  - 无 KMS（AES 密钥从环境变量，TODO 升级腾讯云 KMS Envelope）
  - SMS 验证码固定 1234（开发期，生产对接服务商）
"""
import os
import time
import uuid
import json
import hmac
import base64
import hashlib
import asyncio
from typing import Optional

import bcrypt
from jose import JWTError
from jose import jwt as jose_jwt
from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from pydantic import BaseModel
from pydantic import Field

from .deps import get_current_user
from ..db_platform import create_user
from ..db_platform import get_data_permissions
from ..db_platform import get_user_by_id
from ..db_platform import get_user_by_phone_hash
from ..db_platform import revoke_token
from ..db_platform import set_data_permissions
from ..db_platform import update_user_profile
from ..db_platform import update_user_settings
from ..db_platform import get_user_settings

router = APIRouter(prefix="/api/v1", tags=["auth"])

# ============================================================
# 配置
# ============================================================

# JWT 密钥 — 生产从环境变量，开发默认
JWT_SECRET = os.getenv("PLATFORM_JWT_SECRET", "dev-secret-change-in-prod-please")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL = 15 * 60         # 15 分钟
REFRESH_TOKEN_TTL = 7 * 24 * 3600  # v2 H8: 7 天（非 30 天）

# AES-256-GCM 密钥（32 字节）— 生产从环境变量，TODO 升级 KMS Envelope
_AES_KEY_ENV = os.getenv("PLATFORM_PHONE_AES_KEY", "dev-aes-key-change-in-prod-32bytes!!").encode("utf-8")
AES_KEY = hashlib.sha256(_AES_KEY_ENV).digest()  # 确保正好 32 字节

# SMS 验证码（P0 精简版：固定 1234；生产对接服务商）
SMS_FIXED_CODE = os.getenv("PLATFORM_SMS_FIXED_CODE", "1234")
SMS_CODE_TTL = 5 * 60  # 验证码 5 分钟有效

# SMS 限流（v2: 内存计数，生产换 Redis）
_sms_ip_counter: dict[str, list[float]] = {}
_sms_phone_counter: dict[str, list[float]] = {}
_sms_lock = asyncio.Lock()


# ============================================================
# 手机号 AES-256-GCM 加密（v2: GCM 模式 + 每条独立 IV）
# ============================================================

def _encrypt_phone(phone: str) -> str:
    """AES-256-GCM 加密手机号，返回 base64(iv|ciphertext|tag)。"""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        # 无 cryptography 库时降级为简单混淆（仅开发期，生产必须装 cryptography）
        return base64.b64encode(phone.encode()).decode()
    aesgcm = AESGCM(AES_KEY)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, phone.encode(), None)
    return base64.b64encode(iv + ct).decode()


def _decrypt_phone(phone_enc: str) -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        return base64.b64decode(phone_enc).decode()
    aesgcm = AESGCM(AES_KEY)
    raw = base64.b64decode(phone_enc)
    iv, ct = raw[:12], raw[12:]
    return aesgcm.decrypt(iv, ct, None).decode()


def _hash_phone(phone: str) -> str:
    """手机号单向 hash（用于唯一索引 + 查找），bcrypt 太慢用 sha256+salt。"""
    return hashlib.sha256((phone + JWT_SECRET).encode()).hexdigest()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except (ValueError, TypeError):
        return False


# ============================================================
# JWT 签发
# ============================================================

def _create_token(user_id: int, role: str, ttl: int, token_type: str) -> tuple[str, str, float]:
    """签发 JWT，返回 (token, jti, expires_at)。"""
    jti = uuid.uuid4().hex
    now = time.time()
    expires_at = now + ttl
    payload = {
        "user_id": user_id,
        "role": role,
        "type": token_type,
        "jti": jti,
        "iat": int(now),
        "exp": int(expires_at),
    }
    token = jose_jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return token, jti, expires_at


def create_access_token(user_id: int, role: str) -> tuple[str, float]:
    token, _, exp = _create_token(user_id, role, ACCESS_TOKEN_TTL, "access")
    return token, exp


def create_refresh_token(user_id: int, role: str) -> tuple[str, str, float]:
    token, jti, exp = _create_token(user_id, role, REFRESH_TOKEN_TTL, "refresh")
    return token, jti, exp


def decode_token(token: str) -> dict:
    """解码 JWT，失败抛 JWTError。"""
    return jose_jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])


# ============================================================
# SMS 限流（v2: 1/min/IP + 5/h/phone）
# ============================================================

async def _check_sms_rate_limit(phone: str, client_ip: str) -> Optional[str]:
    """返回错误信息字符串，None 表示通过。"""
    async with _sms_lock:
        now = time.time()
        # IP: 1/min
        ip_hits = [t for t in _sms_ip_counter.get(client_ip, []) if now - t < 60]
        if len(ip_hits) >= 1:
            return "同一 IP 每分钟只能请求 1 次验证码"
        # phone: 5/h
        phone_hits = [t for t in _sms_phone_counter.get(phone, []) if now - t < 3600]
        if len(phone_hits) >= 5:
            return "该手机号一小时内验证码请求已达上限"
        ip_hits.append(now)
        phone_hits.append(now)
        _sms_ip_counter[client_ip] = ip_hits
        _sms_phone_counter[phone] = phone_hits
    return None


# ============================================================
# Pydantic 模型
# ============================================================

class SMSCheck(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11)


class RegisterReq(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11)
    code: str = Field(..., min_length=4, max_length=6)
    nickname: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=8, max_length=20)


class LoginReq(BaseModel):
    phone: str = Field(..., min_length=11, max_length=11)
    code: Optional[str] = Field(None, min_length=4, max_length=6)
    password: Optional[str] = Field(None, min_length=8, max_length=20)


class RefreshReq(BaseModel):
    refresh_token: str


class LogoutReq(BaseModel):
    refresh_token: str


class ProfilePatch(BaseModel):
    nickname: Optional[str] = Field(None, max_length=20)
    avatar_url: Optional[str] = None
    gender: Optional[str] = Field(None, pattern="^(male|female|custom)$")
    custom_gender: Optional[str] = Field(None, max_length=20)
    birthday: Optional[str] = None
    bio: Optional[str] = Field(None, max_length=60)


class SettingsPatch(BaseModel):
    push_notification: Optional[bool] = None
    message_sound: Optional[bool] = None
    vibration: Optional[bool] = None
    ringtone: Optional[str] = None
    chat_bg_type: Optional[str] = None
    chat_bg_value: Optional[str] = None
    theme: Optional[str] = Field(None, pattern="^(light|dark|auto)$")
    font_size: Optional[str] = Field(None, pattern="^(small|medium|large)$")


class DataPermissionsPut(BaseModel):
    ai_training: Optional[bool] = None
    learn_chat_style: Optional[bool] = None
    remember_interests: Optional[bool] = None
    usage_statistics: Optional[bool] = None
    crash_report: Optional[bool] = None
    third_party_sharing: Optional[bool] = None


# ============================================================
# 端点
# ============================================================

@router.post("/auth/sms")
async def send_sms(req: SMSCheck, request: Request):
    """发送短信验证码。P0 精简版：固定 1234，不真发短信。"""
    client_ip = request.client.host if request.client else "unknown"
    err = await _check_sms_rate_limit(req.phone, client_ip)
    if err:
        raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": err})
    # P0 精简版：固定验证码，生产对接服务商
    return {
        "sent": True,
        "cooldown": 60,
        "dev_hint": f"验证码固定为 {SMS_FIXED_CODE}（开发期）",
    }


@router.post("/auth/register")
async def register(req: RegisterReq):
    """注册：手机号 + 验证码 + 昵称 + 密码。"""
    # 验证码校验（P0 固定）
    if req.code != SMS_FIXED_CODE:
        raise HTTPException(status_code=400, detail={"code": "invalid_code", "message": "验证码错误"})
    phone_hash = _hash_phone(req.phone)
    existing = await get_user_by_phone_hash(phone_hash)
    if existing:
        raise HTTPException(status_code=409, detail={"code": "phone_exists", "message": "该手机号已注册"})
    phone_enc = _encrypt_phone(req.phone)
    password_hash = _hash_password(req.password)
    user_id = await create_user(phone_hash, phone_enc, password_hash, req.nickname)
    user = await get_user_by_id(user_id)
    access, _ = create_access_token(user_id, user["is_admin"])
    refresh, _, _ = create_refresh_token(user_id, user["is_admin"])
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "user": _user_public(user),
    }


@router.post("/auth/login")
async def login(req: LoginReq):
    """登录：手机号 + 验证码 或 手机号 + 密码。"""
    phone_hash = _hash_phone(req.phone)
    user = await get_user_by_phone_hash(phone_hash)
    if not user:
        raise HTTPException(status_code=401, detail={"code": "invalid_credentials", "message": "手机号或凭证错误"})
    # 验证码登录
    if req.code:
        if req.code != SMS_FIXED_CODE:
            raise HTTPException(status_code=400, detail={"code": "invalid_code", "message": "验证码错误"})
    # 密码登录
    elif req.password:
        if not _verify_password(req.password, user["password"]):
            raise HTTPException(status_code=401, detail={"code": "invalid_credentials", "message": "手机号或密码错误"})
    else:
        raise HTTPException(status_code=400, detail={"code": "missing_credentials", "message": "需提供验证码或密码"})
    access, _ = create_access_token(user["id"], user["is_admin"])
    refresh, _, _ = create_refresh_token(user["id"], user["is_admin"])
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_TTL,
        "user": _user_public(user),
    }


@router.post("/auth/refresh")
async def refresh_token(req: RefreshReq):
    """刷新 access token。v2 H7: 查黑名单。"""
    try:
        payload = decode_token(req.refresh_token)
    except JWTError:
        raise HTTPException(status_code=401, detail={"code": "invalid_token", "message": "refresh token 无效"})
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail={"code": "invalid_token", "message": "非 refresh token"})
    from ..db_platform import is_token_revoked
    if await is_token_revoked(payload.get("jti", "")):
        raise HTTPException(status_code=401, detail={"code": "token_revoked", "message": "token 已吊销"})
    user_id = payload["user_id"]
    role = payload["role"]
    access, _ = create_access_token(user_id, role)
    return {"access_token": access, "token_type": "bearer", "expires_in": ACCESS_TOKEN_TTL}


@router.post("/auth/logout")
async def logout(req: LogoutReq, user=Depends(get_current_user)):
    """退出登录：v2 H7 立即吊销 refresh token（写入黑名单，无宽限期）。"""
    try:
        payload = decode_token(req.refresh_token)
    except JWTError:
        return {"ok": True}  # 无效 token 视为已退出
    if payload.get("type") == "refresh":
        await revoke_token(payload.get("jti", ""), user["id"], payload.get("exp", 0))
    return {"ok": True}


@router.get("/user/profile")
async def get_profile(user=Depends(get_current_user)):
    return _user_public(user)


@router.patch("/user/profile")
async def patch_profile(req: ProfilePatch, user=Depends(get_current_user)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    if fields:
        await update_user_profile(user["id"], fields)
    updated = await get_user_by_id(user["id"])
    return _user_public(updated)


@router.get("/user/settings")
async def get_settings(user=Depends(get_current_user)):
    return await get_user_settings(user["id"])


@router.patch("/user/settings")
async def patch_settings(req: SettingsPatch, user=Depends(get_current_user)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = await update_user_settings(user["id"], fields)
    return merged


@router.get("/user/data-permissions")
async def get_perms(user=Depends(get_current_user)):
    return await get_data_permissions(user["id"])


@router.put("/user/data-permissions")
async def put_perms(req: DataPermissionsPut, user=Depends(get_current_user)):
    fields = {k: v for k, v in req.model_dump().items() if v is not None}
    merged = await set_data_permissions(user["id"], fields)
    return merged


@router.get("/app/version")
async def app_version():
    return {"version": "1.0.0", "min_version": "1.0.0", "update_url": ""}


# ============================================================
# 辅助
# ============================================================

def _user_public(user: dict) -> dict:
    """脱敏用户对象（不返回 phone_enc/password）。"""
    return {
        "id": user["id"],
        "user_id": f"NianNian{user['id']:06d}",
        "nickname": user["nickname"],
        "avatar_url": user["avatar_url"],
        "gender": user["gender"],
        "custom_gender": user["custom_gender"],
        "birthday": user["birthday"],
        "bio": user["bio"],
        "is_admin": bool(user["is_admin"]),
        "created_at": user["created_at"],
    }
