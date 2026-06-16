"""Admin 管理后台 Bearer Token 认证中间件。

保护 /admin 和 /admin/api/* 端点，要求请求携带有效的 Bearer Token。
/health 端点不受影响。
"""
import os
from typing import Optional

from nonebot import logger


# 从环境变量读取 admin token，未配置则 admin 功能不可用
ADMIN_API_KEY: str = os.getenv("ADMIN_API_KEY", "").strip()

# 认证豁免路径
_EXEMPT_PATHS = ("/health",)


def is_admin_path(path: str) -> bool:
    """判断是否为 admin 路径。"""
    return path == "/admin" or path == "/admin/" or path.startswith("/admin/")


def verify_token(request) -> bool:
    """验证请求的 Bearer Token。返回 True 表示通过。"""
    # 未配置 ADMIN_API_KEY 时，admin 功能完全不可用
    if not ADMIN_API_KEY:
        return False

    # 豁免路径不需要认证
    path = getattr(request, "url", None)
    if path:
        from urllib.parse import urlparse
        parsed = urlparse(str(path))
        if parsed.path in _EXEMPT_PATHS:
            return True

    # 非 admin 路径不需要认证
    if path:
        from urllib.parse import urlparse
        parsed = urlparse(str(path))
        if not is_admin_path(parsed.path):
            return True

    # 检查 Authorization header
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return False

    token = auth_header[7:]  # 去掉 "Bearer " 前缀
    return token == ADMIN_API_KEY


def check_admin_key_configured() -> bool:
    """检查 ADMIN_API_KEY 是否已配置。未配置时输出安全警告。"""
    if not ADMIN_API_KEY:
        logger.warning(
            "[安全] ADMIN_API_KEY 未配置！管理后台将不可用。\n"
            "       请设置环境变量 ADMIN_API_KEY=<随机token> 后重启。\n"
            "       生成随机 token: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
        return False
    logger.info(f"[安全] Admin 认证中间件已启用 (token={ADMIN_API_KEY[:3]}***)")
    return True
