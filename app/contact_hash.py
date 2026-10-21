"""検証済み連絡先の照合用ハッシュ。

保存するのは HMAC-SHA256(サーバー秘密, SHA-256(正規化メール)) だけ。
単純な SHA-256 は保存しない。秘密値は設定から読み、未設定なら計算しない。
"""

from __future__ import annotations

import hashlib
import hmac

from flask import current_app

HASH_VERSION = 2
LEGACY_HASH_VERSION = 1
MIN_KEY_BYTES = 32


class ContactHashKeyError(RuntimeError):
    """照合用の秘密が無い。SHA-256 への代替はしない。"""


def contact_hash_key() -> bytes:
    value = current_app.config.get("CONTACT_HASH_KEY")
    if not isinstance(value, str):
        raise ContactHashKeyError("メール照合の秘密値が設定されていません。")
    key = value.encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise ContactHashKeyError("メール照合の秘密値が設定されていません。")
    return key


def normalize_email(value) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid email")
    normalized = value.strip().lower()
    if "@" not in normalized or len(normalized) > 200 or any(ord(char) < 32 for char in normalized):
        raise ValueError("invalid email")
    return normalized


def _sha256(normalized: str) -> bytes:
    return hashlib.sha256(normalized.encode("utf-8")).digest()


def address_hmac(value: str) -> bytes:
    """正規化したメールから、現在の秘密値で照合用ハッシュを作る。"""
    digest = _sha256(normalize_email(value))
    return hmac.new(contact_hash_key(), digest, hashlib.sha256).digest()


def upgrade_legacy_digest(legacy_sha256: bytes) -> bytes:
    """既存の SHA-256(正規化メール) を HMAC(秘密, そのSHA-256) へ変える。

    元のメールは残っていない。新しい保存値は address_hmac と同じ計算になる。
    """
    material = bytes(legacy_sha256)
    if len(material) != 32:
        raise ValueError("legacy digest length")
    return hmac.new(contact_hash_key(), material, hashlib.sha256).digest()
