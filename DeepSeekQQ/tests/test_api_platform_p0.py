"""P0 三件套端到端集成测试 — 注册→创建Bot→WS聊天。

验证：
  - 1.1 health 端点
  - 1.2 用户系统精简版（SMS/注册/登录/JWT/profile/data-permissions/logout/refresh）
  - 1.4 Bot CRUD 精简版（GET/POST/PUT/DELETE + H5 ownership 校验）
  - 1.6 WS 聊天精简版（方案 B：流式 LLM + 消息历史 + JWT 子协议认证 + client_id 幂等）

v2 审计修正验证：
  - S1: call_deepseek_api_stream 流式 SSE 解析（mock）
  - S3: client_id 幂等去重
  - S5: WS JWT 通过 Sec-WebSocket-protocol 子协议头
  - H5: ownership 校验返回 403 bot_not_owned
  - H7: logout 后 refresh 返回 401 token_revoked
  - H8: refresh TTL 7d
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock
from unittest.mock import patch

import pytest

# 确保能导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# conftest.py 把 fastapi mock 成空壳（为现有 web_admin 测试），
# P0 测试需要真 fastapi + starlette TestClient —— 清除 mock 重新导入。
for _mod in list(sys.modules):
    if _mod == "fastapi" or _mod.startswith("fastapi.") or _mod == "starlette" or _mod.startswith("starlette."):
        # 保留 starlette.testclient（若已是真的）— 只清 fastapi mock
        if _mod == "fastapi" or _mod.startswith("fastapi."):
            del sys.modules[_mod]

# 强制导入真 fastapi
import importlib
import fastapi  # noqa: F401
import fastapi.middleware.cors  # noqa: F401
import fastapi.routing  # noqa: F401

from starlette.testclient import TestClient

from plugins.deepseek.api_platform import server as server_mod
from plugins.deepseek.api_platform.auth import ACCESS_TOKEN_TTL
from plugins.deepseek.api_platform.auth import REFRESH_TOKEN_TTL
from plugins.deepseek.api_platform.auth import create_access_token
from plugins.deepseek.api_platform.auth import create_refresh_token
from plugins.deepseek.api_platform.auth import decode_token
from plugins.deepseek.db_platform import init_platform_tables


@pytest.fixture(scope="module")
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def app_with_tables(tmp_path, monkeypatch):
    """独立 DB + 建表 + TestClient。"""
    db_path = str(tmp_path / "test_platform.db")
    monkeypatch.setenv("DEEPSEEK_DB_PATH", db_path)
    # 重置 db_core 全局连接
    from plugins.deepseek import db_core
    if db_core._db is not None:
        await db_core._db.close()
        db_core._db = None
    await init_platform_tables()
    yield
    if db_core._db is not None:
        await db_core._db.close()
        db_core._db = None


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_health(app_with_tables):
    """1.1 health 端点。"""
    client = TestClient(server_mod.app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    d = r.json()
    assert d["ok"] is True
    assert d["version"] == "1.0.0"
    assert d["min_app_version"] == "1.0.0"


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_register_and_login(app_with_tables):
    """1.2 注册 + 验证码登录 + 密码登录。"""
    client = TestClient(server_mod.app)
    # 注册
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800138000", "code": "1234",
        "nickname": "测试", "password": "Test1234pass",
    })
    assert r.status_code == 200, r.json()
    d = r.json()
    assert "access_token" in d
    assert "refresh_token" in d
    assert d["user"]["nickname"] == "测试"
    assert d["user"]["user_id"].startswith("NianNian")
    token = d["access_token"]

    # 重复注册 → 409
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800138000", "code": "1234",
        "nickname": "重复", "password": "Test1234pass",
    })
    assert r.status_code == 409

    # 错误验证码 → 400
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800138001", "code": "9999",
        "nickname": "错码", "password": "Test1234pass",
    })
    assert r.status_code == 400

    # 密码登录
    r = client.post("/api/v1/auth/login", json={
        "phone": "13800138000", "password": "Test1234pass",
    })
    assert r.status_code == 200
    assert r.json()["access_token"]

    # 验证码登录
    r = client.post("/api/v1/auth/login", json={
        "phone": "13800138000", "code": "1234",
    })
    assert r.status_code == 200

    # 错误密码 → 401
    r = client.post("/api/v1/auth/login", json={
        "phone": "13800138000", "password": "wrongpassword",
    })
    assert r.status_code == 401


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_jwt_ttl_and_decode(app_with_tables):
    """H8: refresh TTL 7d（非 30d）。JWT 解码正确。"""
    assert REFRESH_TOKEN_TTL == 7 * 24 * 3600, "refresh TTL 必须是 7 天（H8）"
    assert ACCESS_TOKEN_TTL == 15 * 60, "access TTL 必须是 15 分钟"
    access, _ = create_access_token(42, 0)
    payload = decode_token(access)
    assert payload["user_id"] == 42
    assert payload["type"] == "access"
    refresh, jti, _ = create_refresh_token(42, 0)
    payload = decode_token(refresh)
    assert payload["type"] == "refresh"
    assert payload["jti"] == jti


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_logout_revokes_refresh(app_with_tables):
    """H7: logout 写黑名单，之后 refresh 返回 401 token_revoked。"""
    client = TestClient(server_mod.app)
    # 注册
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800138888", "code": "1234",
        "nickname": "吊销测试", "password": "Test1234pass",
    })
    d = r.json()
    token = d["access_token"]
    refresh = d["refresh_token"]
    H = {"Authorization": f"Bearer {token}"}

    # logout
    r = client.post("/api/v1/auth/logout", headers=H, json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # 吊销后刷新 → 401
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    assert r.json()["detail"]["code"] == "token_revoked"


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_bot_crud_and_ownership(app_with_tables):
    """1.4 Bot CRUD + H5 ownership 校验。"""
    client = TestClient(server_mod.app)
    # 用户 A
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800130001", "code": "1234",
        "nickname": "用户A", "password": "Test1234pass",
    })
    token_a = r.json()["access_token"]
    uid_a = r.json()["user"]["id"]
    H_a = {"Authorization": f"Bearer {token_a}"}

    # 用户 B
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800130002", "code": "1234",
        "nickname": "用户B", "password": "Test1234pass",
    })
    token_b = r.json()["access_token"]
    H_b = {"Authorization": f"Bearer {token_b}"}

    # A 创建 Bot
    r = client.post("/api/v1/bots", headers=H_a, json={
        "name": "小咪", "personality": "tsundere", "catchphrase": "哼！", "age": 17,
    })
    assert r.status_code == 201
    bot_id = r.json()["id"]
    assert r.json()["personality_label"] == "傲娇"

    # A 列出 Bot
    r = client.get("/api/v1/bots", headers=H_a)
    assert r.status_code == 200
    assert r.json()["count"] == 1

    # A 改 Bot 滑块
    r = client.put("/api/v1/bots/{0}".format(bot_id), headers=H_a, json={
        "style_score": 8, "talkativeness": 6, "call_preference": "master",
    })
    assert r.status_code == 200
    persona = r.json()["persona"]
    assert persona["style_score"] == 8
    assert persona["call_preference"] == "master"

    # B 访问 A 的 Bot → 403 bot_not_owned（H5）
    r = client.get(f"/api/v1/bots/{bot_id}", headers=H_b)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "bot_not_owned"

    # B 列出 Bot → count 0（H5 自动过滤）
    r = client.get("/api/v1/bots", headers=H_b)
    assert r.json()["count"] == 0

    # A 删除 Bot
    r = client.delete(f"/api/v1/bots/{bot_id}", headers=H_a)
    assert r.status_code == 204

    # 删除后 A 列出 → 0
    r = client.get("/api/v1/bots", headers=H_a)
    assert r.json()["count"] == 0


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_ws_chat_streaming_and_idempotency(app_with_tables):
    """1.6 WS 聊天 + S1 流式 + S3 client_id 幂等 + S5 子协议认证。"""
    client = TestClient(server_mod.app)
    # 注册 + 建 Bot
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800130999", "code": "1234",
        "nickname": "WS用户", "password": "Test1234pass",
    })
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}
    r = client.post("/api/v1/bots", headers=H, json={"name": "WSBot", "personality": "gentle"})
    bot_id = r.json()["id"]

    # mock 流式 LLM（避免依赖真实 API key）
    async def fake_stream(messages):
        for chunk in ["你好", "，我是", "小喵", "~"]:
            yield chunk

    with patch(
        "plugins.deepseek.api_platform.chat.call_deepseek_api_stream",
        new=fake_stream,
    ):
        # WS 连接（S5: 子协议头传 token）
        with client.websocket_connect(
            "/api/v1/chat/ws", subprotocols=[f"bearer.{token}"]
        ) as ws:
            # 发消息
            ws.send_text(json.dumps({
                "type": "msg", "bot_id": bot_id,
                "text": "你好", "client_id": "ws-test-001",
            }))

            # 收帧
            ack_seen = False
            typing_seen = False
            tokens = []
            done_seen = False
            for _ in range(20):
                raw = ws.receive_text()
                frame = json.loads(raw)
                ftype = frame.get("type")
                if ftype == "ack":
                    ack_seen = True
                    assert frame["client_id"] == "ws-test-001"
                    assert frame["duplicate"] is False
                elif ftype == "typing":
                    typing_seen = True
                elif ftype == "token":
                    tokens.append(frame["text"])
                elif ftype == "done":
                    done_seen = True
                    assert frame["client_id"] == "ws-test-001"
                    assert "server_id" in frame
                    break

            assert ack_seen, "未收到 ack 帧"
            assert typing_seen, "未收到 typing 帧"
            assert tokens == ["你好", "，我是", "小喵", "~"], f"流式 token 不完整: {tokens}"
            assert done_seen, "未收到 done 帧"

            # S3 幂等：重发同一 client_id
            ws.send_text(json.dumps({
                "type": "msg", "bot_id": bot_id,
                "text": "你好", "client_id": "ws-test-001",
            }))
            raw = ws.receive_text()
            frame = json.loads(raw)
            assert frame["type"] == "ack"
            assert frame["duplicate"] is True, "重发应命中幂等"

    # 消息历史应有 2 条（user + bot）
    r = client.get(f"/api/v1/messages?bot_id={bot_id}", headers=H)
    msgs = r.json()["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] in ("user", "bot")


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_ws_rejects_no_token(app_with_tables):
    """S5: WS 无 token 拒绝连接。"""
    client = TestClient(server_mod.app)
    with pytest.raises(Exception):
        with client.websocket_connect("/api/v1/chat/ws") as ws:
            ws.receive_text()


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_data_permissions_6_switches(app_with_tables):
    """S7: data_permissions 6 开关存储。"""
    client = TestClient(server_mod.app)
    r = client.post("/api/v1/auth/register", json={
        "phone": "13800130777", "code": "1234",
        "nickname": "权限用户", "password": "Test1234pass",
    })
    token = r.json()["access_token"]
    H = {"Authorization": f"Bearer {token}"}

    # 默认值
    r = client.get("/api/v1/user/data-permissions", headers=H)
    perms = r.json()
    expected = {"ai_training", "learn_chat_style", "remember_interests",
                "usage_statistics", "crash_report", "third_party_sharing"}
    assert set(perms.keys()) == expected, f"必须是 6 开关: {set(perms.keys())}"
    assert perms["ai_training"] is True
    assert perms["third_party_sharing"] is False  # 默认关

    # 修改
    r = client.put("/api/v1/user/data-permissions", headers=H,
                   json={"ai_training": False, "third_party_sharing": True})
    perms = r.json()
    assert perms["ai_training"] is False
    assert perms["third_party_sharing"] is True
    # 其他不变
    assert perms["learn_chat_style"] is True


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_sms_rate_limit(app_with_tables):
    """SMS 限流：1/min/IP。"""
    client = TestClient(server_mod.app)
    # 第一次通过
    r = client.post("/api/v1/auth/sms", json={"phone": "13800130666"})
    assert r.status_code == 200
    # 第二次同 IP → 429
    r = client.post("/api/v1/auth/sms", json={"phone": "13800130667"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
