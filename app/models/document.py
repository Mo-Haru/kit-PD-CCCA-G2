"""本人用データ鍵のメタデータ・鍵envelope・暗号化回答文書。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedMixin, IdentityMixin, Model, UTCDateTime


class UserDataKey(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "user_data_keys"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("user_id", "key_version"),
        CheckConstraint("key_version >= 1", name="key_version_positive"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    key_version: Mapped[int] = mapped_column(Integer)
    # retiredは新規暗号化に使わない意味。過去文書の復号には必要。
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UserKeyEnvelope(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "user_key_envelopes"
    __table_args__ = (
        UniqueConstraint("data_key_id", "passkey_id"),
        ForeignKeyConstraint(["data_key_id", "user_id"], ["user_data_keys.id", "user_data_keys.user_id"]),
        ForeignKeyConstraint(["passkey_id", "user_id"], ["passkey_credentials.id", "passkey_credentials.user_id"]),
        CheckConstraint("length(nonce) = 12", name="nonce_length"),
        CheckConstraint("length(prf_input) = 32 AND length(kdf_salt) = 32", name="salt_lengths"),
        CheckConstraint("length(wrapped_key) = 48", name="wrapped_key_length"),
    )

    # 冗長にuser_idを持たせ、複合外部キーで別人の鍵との関連を拒否。
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    data_key_id: Mapped[str] = mapped_column(String(36))
    passkey_id: Mapped[str] = mapped_column(String(36), index=True)
    prf_input: Mapped[bytes] = mapped_column(LargeBinary(32))
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary(32))
    kdf_version: Mapped[int] = mapped_column(Integer, default=1)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    crypto_version: Mapped[int] = mapped_column(Integer, default=1)


class PrivateAnswerDocument(IdentityMixin, CreatedMixin, Model):
    """回答とノート。改訂ごとにINSERTし、UPDATEはサービス層で禁止する。"""

    __tablename__ = "private_answer_documents"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        UniqueConstraint("user_id", "revision"),
        UniqueConstraint("data_key_id", "nonce"),
        ForeignKeyConstraint(["data_key_id", "user_id"], ["user_data_keys.id", "user_data_keys.user_id"]),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("length(nonce) = 12", name="nonce_length"),
        CheckConstraint("length(ciphertext) >= 16", name="ciphertext_length"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    data_key_id: Mapped[str] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    crypto_version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)


class RecoveryKeyEnvelope(IdentityMixin, CreatedMixin, Model):
    """高エントロピー復旧コードによる鍵復旧。ログイン迂回には使わない。"""

    __tablename__ = "recovery_key_envelopes"
    __table_args__ = (
        ForeignKeyConstraint(["data_key_id", "user_id"], ["user_data_keys.id", "user_data_keys.user_id"]),
        CheckConstraint("length(nonce) = 12 AND length(kdf_salt) = 32", name="lengths"),
        CheckConstraint("length(wrapped_key) = 48", name="wrapped_key_length"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    data_key_id: Mapped[str] = mapped_column(String(36), index=True)
    # 端末で生成した256-bitランダム復旧コードからHKDFで導出する想定。
    # 人が考えた短いパスワードをこの方式に代入しない。
    kdf_salt: Mapped[bytes] = mapped_column(LargeBinary(32))
    kdf_version: Mapped[int] = mapped_column(Integer, default=1)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    crypto_version: Mapped[int] = mapped_column(Integer, default=1)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
