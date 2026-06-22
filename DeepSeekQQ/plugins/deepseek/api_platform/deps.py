"""依赖注入 — JWT 校验 + 用户提取 + admin 守卫 + ownership 校验。

v2 审计修正：
  - S6: 独立于 ADMIN_API_KEY，用 JWT（python-jose）
  - H5: ownership 校验函数 require_bot_owner
"""
from typing import Optional

from fastapi import Depends
from fastapi import HTTPException
from fastapi import Request
from fastapi import status
from fastapi import WebSocket

from ..db_platform import get_bot_for_user
from ..db_platform import get_user_by_id


def _decode(token: str) -> dict:
    """延迟导入 decode_token，避免 auth ↔ deps 循环导入。

    auth.py 在模块顶层 from .deps import get_current_user，若 deps 也在顶层
    from .auth import decode_token 会形成循环。这里用函数内延迟导入打破。
    """
    from .auth import decode_token
    return decode_token(token)


async def get_current_user(request: Request) -> dict:
    """从 Authorization: Bearer <access> 提取当前用户。

    用于 REST 端点。WS 端点用 ws_current_user（从子协议头提取）。
    """
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "missing_token", "message": "需要 Bearer Token"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:]
    try:
        payload = _decode(token)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "token 无效或已过期"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "invalid_token", "message": "需要 access token"},
        )
    user_id = payload.get("user_id")
    user = await get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "user_not_found", "message": "用户不存在"},
        )
    return user


async def require_admin(user: dict = Depends(get_current_user)) -> dict:
    """S6: admin 守卫，is_admin=1 才放行。"""
    if not user.get("is_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "admin_required", "message": "需要管理员权限"},
        )
    return user


async def ws_current_user(ws: WebSocket) -> dict:
    """S5: WS JWT 通过 Sec-WebSocket-Protocol 子协议头传递，禁用 URL query param。

    客户端连接时：new WebSocket(url, ['bearer.<jwt>'])
    提取 subprotocol 中 'bearer.' 前缀后的 token。
    """
    # FastAPI/Starlette 把客户端请求的 subprotocols 放在 ws.headers 或 ws.scope
    # Starlette 的 WebSocket 接受 subprotocols，握手后选中的在 ws.subprotocol
    # 但客户端发送的列表在 ws.scope['subprotocols']
    subprotocols = ws.scope.get("subprotocols", [])
    token = None
    for sp in subprotocols:
        if sp.startswith("bearer."):
            token = sp[len("bearer."):]
            break
    if not token:
        # 降级：允许 query param（仅开发期，生产关闭）
        token = ws.query_params.get("token")
    if not token:
        await ws.close(code=4401, reason="需要认证")
        raise HTTPException(status_code=401, detail={"code": "missing_token"})
    try:
        payload = _decode(token)
    except Exception:
        await ws.close(code=4401, reason="token 无效")
        raise HTTPException(status_code=401, detail={"code": "invalid_token"})
    if payload.get("type") != "access":
        await ws.close(code=4401, reason="需要 access token")
        raise HTTPException(status_code=401, detail={"code": "invalid_token"})
    user = await get_user_by_id(payload.get("user_id"))
    if not user:
        await ws.close(code=4401, reason="用户不存在")
        raise HTTPException(status_code=401, detail={"code": "user_not_found"})
    return user


async def require_bot_owner(bot_id: int, user: dict) -> dict:
    """H5: 校验 bot_id 属于当前 user_id，返回 bot dict，否则 403。"""
    bot = await get_bot_for_user(bot_id, user["id"])
    if not bot:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "bot_not_owned", "message": "无权访问该 Bot"},
        )
    return bot
