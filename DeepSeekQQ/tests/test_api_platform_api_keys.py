"""Task 1.3 API Key 管理 端到端测试。

验证：
  - CRUD：POST 创建 → GET 列表只见 key_suffix → revoke → status 变 revoked
  - H5 ownership：用户 B 吊销 A 的 key → 403 apikey_not_owned
  - 加密往返：decrypt(encrypt(k)) == k，密文 ≠ 明文
  - 安全：GET 列表响应不含 encrypted_key / key_value 明文
  - provider 白名单：非法 provider → 422
  - usage-summary：返回 {total_keys, active_keys, providers}
"""
import json
import sys
from pathlib import Path

import pytest

# 确保能导入
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# conftest.py 把 fastapi mock 成空壳（为现有 web_admin 测试），
# P0 测试需要真 fastapi + starlette TestClient —— 清除 mock 重新导入。
for _mod in list(sys.modules):
    if _mod == "fastapi" or _mod.startswith("fastapi."):
        del sys.modules[_mod]

import fastapi  # noqa: F401
import fastapi.middleware.cors  # noqa: F401
import fastapi.routing  # noqa: F401

from starlette.testclient import TestClient

from plugins.deepseek.api_platform import server as server_mod
from plugins.deepseek.api_platform.kms import decrypt_api_key
from plugins.deepseek.api_platform.kms import encrypt_api_key
from plugins.deepseek.db_platform import init_platform_tables


@pytest.fixture
async def app_with_tables(tmp_path, monkeypatch):
    """独立 DB + 建表 + TestClient（沿用 test_api_platform_p0.py 模式）。"""
    db_path = str(tmp_path / "test_apikey.db")
    monkeypatch.setenv("DEEPSEEK_DB_PATH", db_path)
    from plugins.deepseek import db_core
    if db_core._db is not None:
        await db_core._db.close()
        db_core._db = None
    await init_platform_tables()
    yield
    if db_core._db is not None:
        await db_core._db.close()
        db_core._db = None


def _register_user(client, phone="13800139000", nickname="Key用户"):
    """注册并返回 (token, headers)。"""
    r = client.post("/api/v1/auth/register", json={
        "phone": phone, "code": "1234",
        "nickname": nickname, "password": "Test1234pass",
    })
    assert r.status_code == 200, r.json()
    token = r.json()["access_token"]
    return token, {"Authorization": f"Bearer {token}"}


# ============================================================
# 测试
# ============================================================

@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_api_key_crud(app_with_tables):
    """POST 创建 → GET 列表只见 key_suffix → revoke → status 变 revoked。"""
    client = TestClient(server_mod.app)
    _, H = _register_user(client)

    # POST 创建
    r = client.post("/api/v1/api-keys", headers=H, json={
        "name": "生产环境",
        "provider": "deepseek",
        "key_value": "sk-prod-8a3f2c91d9e7b6a5",
        "scopes": ["chat", "image", "voice"],
    })
    assert r.status_code == 201, r.json()
    d = r.json()
    assert d["key_suffix"] == "b6a5"
    key_id = d["id"]
    # 创建响应不应回传完整 key
    assert "key_value" not in d
    assert "encrypted_key" not in d

    # GET 列表
    r = client.get("/api/v1/api-keys", headers=H)
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["key_suffix"] == "b6a5"
    assert keys[0]["status"] == "active"
    assert keys[0]["scopes"] == ["chat", "image", "voice"]
    assert keys[0]["provider"] == "deepseek"

    # revoke
    r = client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=H)
    assert r.status_code == 200
    assert r.json()["status"] == "revoked"

    # 再列表 → status 变 revoked
    r = client.get("/api/v1/api-keys", headers=H)
    assert r.json()["keys"][0]["status"] == "revoked"
    assert r.json()["keys"][0]["is_active"] is False


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_api_key_ownership(app_with_tables):
    """H5: 用户 B 吊销 A 的 key → 403 apikey_not_owned。"""
    client = TestClient(server_mod.app)
    _, H_a = _register_user(client, "13800139001", "用户A")
    _, H_b = _register_user(client, "13800139002", "用户B")

    # A 创建 key
    r = client.post("/api/v1/api-keys", headers=H_a, json={
        "name": "A的key", "provider": "deepseek", "key_value": "sk-a-1234567890abcdef",
    })
    key_id = r.json()["id"]

    # B 吊销 A 的 key → 403
    r = client.post(f"/api/v1/api-keys/{key_id}/revoke", headers=H_b)
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "apikey_not_owned"

    # B 访问 A 的 key usage → 403
    r = client.get(f"/api/v1/api-keys/{key_id}/usage", headers=H_b)
    assert r.status_code == 403

    # B 列表看不到 A 的 key
    r = client.get("/api/v1/api-keys", headers=H_b)
    assert r.json()["count"] == 0


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_api_key_encryption_roundtrip(app_with_tables):
    """kms: decrypt(encrypt(k)) == k，密文 ≠ 明文。"""
    plaintext = "sk-test-abcdef1234567890"
    ciphertext = encrypt_api_key(plaintext)
    assert ciphertext != plaintext, "密文不能等于明文"
    assert "sk-test" not in ciphertext, "密文不能含明文片段"
    assert decrypt_api_key(ciphertext) == plaintext, "解密往返必须还原明文"

    # 两次加密同一明文应产生不同密文（IV 随机）
    ct2 = encrypt_api_key(plaintext)
    assert ct2 != ciphertext, "随机 IV 应使两次密文不同"


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_api_key_never_exposed(app_with_tables):
    """安全：GET 列表 + usage-summary 响应不含 encrypted_key / key_value 明文。"""
    client = TestClient(server_mod.app)
    _, H = _register_user(client)

    client.post("/api/v1/api-keys", headers=H, json={
        "name": "安全测试", "provider": "kimi",
        "key_value": "sk-never-expose-this-plaintext-123456",
    })

    # 列表响应
    r = client.get("/api/v1/api-keys", headers=H)
    body_text = json.dumps(r.json())
    assert "sk-never-expose-this-plaintext" not in body_text, "完整 key 泄露到列表响应"
    assert "encrypted_key" not in body_text, "encrypted_key 字段不应下发"

    # usage-summary 响应
    r = client.get("/api/v1/api-keys/usage-summary", headers=H)
    body_text = json.dumps(r.json())
    assert "sk-never-expose" not in body_text


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_api_key_provider_whitelist(app_with_tables):
    """非法 provider → 422；非法 scope → 422。"""
    client = TestClient(server_mod.app)
    _, H = _register_user(client)

    # 非法 provider
    r = client.post("/api/v1/api-keys", headers=H, json={
        "name": "非法", "provider": "openai_not_allowed", "key_value": "sk-xxxxxxxx12345678",
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_provider"

    # 合法 provider + 非法 scope
    r = client.post("/api/v1/api-keys", headers=H, json={
        "name": "非法scope", "provider": "deepseek",
        "key_value": "sk-xxxxxxxx12345678", "scopes": ["chat", "hack_system"],
    })
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_scope"


@pytest.mark.asyncio
@pytest.mark.needs_db
async def test_usage_summary_and_endpoints(app_with_tables):
    """usage-summary + usage + endpoints 端点可调，返回结构正确（占位值）。"""
    client = TestClient(server_mod.app)
    _, H = _register_user(client)
    r = client.post("/api/v1/api-keys", headers=H, json={
        "name": "概览测试", "provider": "deepseek", "key_value": "sk-summary-12345678",
    })
    key_id = r.json()["id"]

    # usage-summary
    r = client.get("/api/v1/api-keys/usage-summary", headers=H)
    assert r.status_code == 200
    s = r.json()
    assert s["total_keys"] == 1
    assert s["active_keys"] == 1
    assert "deepseek" in s["providers"]

    # usage（7 天骨架）
    r = client.get(f"/api/v1/api-keys/{key_id}/usage?range=7d", headers=H)
    assert r.status_code == 200
    assert len(r.json()["daily"]) == 7
    assert all(d["calls"] == 0 for d in r.json()["daily"])

    # endpoints
    r = client.get(f"/api/v1/api-keys/{key_id}/endpoints", headers=H)
    assert r.status_code == 200
    assert r.json()["endpoints"] == []
