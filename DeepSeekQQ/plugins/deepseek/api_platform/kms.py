"""API Key 加密存储 — AES-256-GCM + KMS 升级接口。

Task 1.3 决策：本地 AES-256-GCM（复用 auth.py 手机号加密同款 cryptography 库），
主密钥从环境变量 ``PLATFORM_APIKEY_AES_KEY`` 读取（缺失时用开发默认值并打印警告）。

封装 ``KMSClient`` 抽象，后续可平滑升级到腾讯云 KMS Envelope Encryption：
只需把 ``KMSClient.encrypt/decrypt`` 内部改为调 KMS API，调用方（api_keys.py）零改动。

安全要点：
  - 每条 API Key 独立 IV（12 字节随机），GCM 模式自带完整性校验（tag）
  - 主密钥永不落盘到代码，仅从环境变量读取
  - 前端永远只拿到 ``key_suffix``（后 4 位），完整 key 仅在创建时回传一次
  - 主密钥丢失 = 所有已存密文不可逆，部署时务必备份 ``.api.env``
"""
import base64
import hashlib
import logging
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_log = logging.getLogger("api_platform.kms")

# 主密钥派生（与 auth.py 同模式：sha256 确保正好 32 字节 = AES-256）
_AES_KEY_ENV = os.getenv("PLATFORM_APIKEY_AES_KEY", "").strip()
if not _AES_KEY_ENV:
    _log.warning(
        "[KMS] PLATFORM_APIKEY_AES_KEY 未设置，使用开发默认密钥。生产环境必须设置！"
    )
    _AES_KEY_ENV = "dev-apikey-aes-change-in-prod-32bytes!!"
_AES_KEY = hashlib.sha256(_AES_KEY_ENV.encode("utf-8")).digest()


class KMSClient:
    """对称加密客户端。

    升级到腾讯云 KMS 时，把 ``encrypt/decrypt`` 改为调 KMS Envelope：
    1. KMS 生成数据密钥 DEK（明文 + 密文）
    2. 用 DEK 明文本地 AES-GCM 加密 API Key
    3. 存储 DEK 密文 + API Key 密文
    解密时反向。调用方接口不变。
    """

    def __init__(self, key: bytes = _AES_KEY):
        self._aesgcm = AESGCM(key)

    def encrypt(self, plaintext: str) -> str:
        """加密，返回 base64(iv | ciphertext | tag)。"""
        iv = os.urandom(12)
        ct = self._aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
        return base64.b64encode(iv + ct).decode("ascii")

    def decrypt(self, ciphertext_b64: str) -> str:
        """解密 base64(iv | ciphertext | tag)，返回明文。"""
        raw = base64.b64decode(ciphertext_b64)
        iv, ct = raw[:12], raw[12:]
        return self._aesgcm.decrypt(iv, ct, None).decode("utf-8")


_kms = KMSClient()


def encrypt_api_key(key_value: str) -> str:
    """加密单个 API Key 明文。"""
    return _kms.encrypt(key_value)


def decrypt_api_key(ciphertext: str) -> str:
    """解密 API Key 密文（仅在需要用 key 调上游 API 时解密）。"""
    return _kms.decrypt(ciphertext)


def key_suffix(key_value: str) -> str:
    """后 4 位用于前端展示（前端永不返回完整 key）。"""
    return key_value[-4:] if len(key_value) >= 4 else "****"
