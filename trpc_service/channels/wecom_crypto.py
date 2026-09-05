"""企业微信回调加解密（官方协议实现）。

- 验签：sha1(sorted([token, timestamp, nonce, encrypt]))
- 解密：AES-256-CBC（IV=key[:16]），明文 = random(16B) + len(4B BE) + msg + receiveid
- 加密：随机前缀 + 长度 + 消息 + receiveid，PKCS7 填充后 AES 加密
"""
from __future__ import annotations

import base64
import hashlib
import os
import struct
from typing import Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeComCryptoError(Exception):
    """企业微信加解密失败。"""


def sha1_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """官方签名算法：字典序拼接后 SHA1。"""
    raw = "".join(sorted([token or "", timestamp or "", nonce or "", encrypt or ""]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def verify_signature(token: str, timestamp: str, nonce: str, encrypt: str, msg_signature: str) -> bool:
    return sha1_signature(token, timestamp, nonce, encrypt) == (msg_signature or "")


def _aes_key(encode_aes_key: str) -> bytes:
    try:
        return base64.b64decode(encode_aes_key + "=")
    except Exception as ex:  # noqa: BLE001
        raise WeComCryptoError(f"EncodingAESKey 非法: {ex}") from ex


def decrypt_message(encode_aes_key: str, encrypt_b64: str) -> Tuple[str, str]:
    """解密回调报文，返回 (消息 XML, receiveid)。"""
    key = _aes_key(encode_aes_key)
    try:
        cipher_text = base64.b64decode(encrypt_b64)
        decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
        padded = decryptor.update(cipher_text) + decryptor.finalize()
    except Exception as ex:  # noqa: BLE001
        raise WeComCryptoError(f"AES 解密失败: {ex}") from ex
    pad = padded[-1]
    if not 1 <= pad <= 32:
        raise WeComCryptoError("PKCS7 填充非法")
    plain = padded[:-pad]
    msg_len = struct.unpack(">I", plain[16:20])[0]
    msg = plain[20:20 + msg_len]
    receiveid = plain[20 + msg_len:]
    return msg.decode("utf-8"), receiveid.decode("utf-8")


def encrypt_message(encode_aes_key: str, message: str, receiveid: str) -> str:
    """加密回复报文，返回 base64 密文。"""
    key = _aes_key(encode_aes_key)
    msg_bytes = message.encode("utf-8")
    receive_bytes = receiveid.encode("utf-8")
    payload = os.urandom(16) + struct.pack(">I", len(msg_bytes)) + msg_bytes + receive_bytes
    pad_len = 32 - (len(payload) % 32)
    payload += bytes([pad_len]) * pad_len
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    cipher_text = encryptor.update(payload) + encryptor.finalize()
    return base64.b64encode(cipher_text).decode("utf-8")
