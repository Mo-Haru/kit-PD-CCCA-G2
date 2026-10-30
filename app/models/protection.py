"""情報保護・家族共有・QR閲覧・端末移行で不足していたテーブル。

既存の19モデルは変更しない。共有先ごとのHPKE暗号文、QRの開始トークン、
閲覧セッション、移行状態は、既存のSharedSnapshotの固定長ラップでは表せない。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.crypto_contract import HPKE_SUITE
from app.models.base import (
    CreatedMixin,
    IdentityMixin,
    Model,
    UpdatedMixin,
    UTCDateTime,
    new_share_id,
    utcnow,
)


class SchemaMigration(Model):
    """適用済みのスキーマ手順。既存行の削除や既存テーブルの作り直しはしない。"""

    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(80), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UserEncryptionKey(IdentityMixin, CreatedMixin, Model):
    """受信用の公開鍵。パスキーの認証用公開鍵とは別物。"""

    __tablename__ = "user_encryption_keys"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("purpose IN ('receive')", name="purpose"),
        CheckConstraint("status IN ('active', 'retired')", name="status"),
        CheckConstraint("length(public_key) = 32", name="public_key_length"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    purpose: Mapped[str] = mapped_column(String(16), default="receive")
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32))
    status: Mapped[str] = mapped_column(String(16), default="active")
    retired_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class UserKeyBundle(IdentityMixin, CreatedMixin, Model):
    """保管用鍵で包んだ鍵束。秘密鍵の平文は入らない。"""

    __tablename__ = "user_key_bundles"
    __table_args__ = (
        UniqueConstraint("user_id", "data_key_id", "revision"),
        ForeignKeyConstraint(
            ["data_key_id", "user_id"],
            ["user_data_keys.id", "user_data_keys.user_id"],
        ),
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


class SessionReauth(Model):
    """セッション発行後の再認証。失効したパスキーの再認証は使わない。"""

    __tablename__ = "session_reauths"

    session_id: Mapped[str] = mapped_column(ForeignKey("auth_sessions.id"), primary_key=True)
    passkey_id: Mapped[str] = mapped_column(String(36), index=True)
    reauthenticated_at: Mapped[datetime] = mapped_column(UTCDateTime())


class RateLimitBucket(Model):
    __tablename__ = "rate_limit_buckets"

    bucket_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    # 今の窓で断った回数。監査ログは窓ごとに最初の1回だけ残し、残りはこの数にまとめる。
    denied_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class ShareQuotaLock(Model):
    """有効な共有の枠を確かめて作成するまで、同じアカウントの操作を1件ずつにするためのロック行。"""

    __tablename__ = "share_quota_locks"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, default=0)


class VerifiedContact(IdentityMixin, CreatedMixin, Model):
    """検証済み連絡先。平文のメールアドレスは保存しない。"""

    __tablename__ = "verified_contacts"
    __table_args__ = (
        UniqueConstraint("kind", "address_hash"),
        CheckConstraint("kind IN ('email')", name="kind"),
        CheckConstraint("length(address_hash) = 32", name="address_hash_length"),
        CheckConstraint("address_hash_version IN (1, 2)", name="address_hash_version"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    address_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    # 1 は移行前の SHA-256。2 は HMAC-SHA256(秘密, SHA-256)。移行後に 1 は残さない。
    address_hash_version: Mapped[int] = mapped_column(Integer, default=2)
    verified_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class FamilyInvitation(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "family_invitations"
    __table_args__ = (
        CheckConstraint("kind IN ('open', 'email')", name="kind"),
        CheckConstraint(
            "status IN ('pending', 'awaiting_sender', 'accepted', 'rejected', 'cancelled', 'expired')",
            name="status",
        ),
        CheckConstraint("length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry"),
        CheckConstraint(
            "kind != 'email' OR address_hash IS NOT NULL",
            name="email_hash_required",
        ),
        Index("uq_family_invitations_confirm_token", "confirm_token_hash", unique=True),
    )

    sender_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    address_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    address_hash_version: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    recipient_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 申請した人だけが受け取る確認リンクとコード。招待した人はこれを受け取って承認する。
    confirm_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    confirm_code_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    confirm_failures: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class FamilyConnection(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "family_connections"
    __table_args__ = (
        UniqueConstraint("invitation_id"),
        CheckConstraint("status IN ('active', 'disconnected')", name="status"),
        CheckConstraint("user_low_id < user_high_id", name="pair_order"),
        Index(
            "uq_family_connection_active_pair",
            "user_low_id",
            "user_high_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )

    invitation_id: Mapped[str] = mapped_column(ForeignKey("family_invitations.id"))
    user_low_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    user_high_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    disconnected_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    disconnected_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))


class FamilyShare(IdentityMixin, CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "family_shares"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'stopped', 'key_pending')", name="status"),
        CheckConstraint("current_version >= 1", name="version_positive"),
        CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="expiry"),
    )

    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    connection_id: Mapped[str] = mapped_column(ForeignKey("family_connections.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="active")
    recipient_key_id: Mapped[str] = mapped_column(ForeignKey("user_encryption_keys.id"))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class FamilyShareVersion(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "family_share_versions"
    __table_args__ = (
        UniqueConstraint("share_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(hpke_enc) = 32", name="enc_length"),
        CheckConstraint("length(ciphertext) >= 16", name="ciphertext_length"),
    )

    share_id: Mapped[str] = mapped_column(ForeignKey("family_shares.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    hpke_enc: Mapped[bytes] = mapped_column(LargeBinary(32))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    crypto_suite: Mapped[str] = mapped_column(String(80), default=HPKE_SUITE)


class FamilyShareOwnerCopy(IdentityMixin, CreatedMixin, Model):
    """所有者が自分の保管用鍵で読む履歴。受信者の秘密鍵は不要。"""

    __tablename__ = "family_share_owner_copies"
    __table_args__ = (
        UniqueConstraint("share_id", "version"),
        ForeignKeyConstraint(
            ["data_key_id", "owner_user_id"],
            ["user_data_keys.id", "user_data_keys.user_id"],
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(nonce) = 12", name="nonce_length"),
        CheckConstraint("length(ciphertext) >= 16", name="ciphertext_length"),
    )

    share_id: Mapped[str] = mapped_column(ForeignKey("family_shares.id"), index=True)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    data_key_id: Mapped[str] = mapped_column(String(36))
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    crypto_version: Mapped[int] = mapped_column(Integer, default=1)


class QrShare(CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "qr_shares"
    __table_args__ = (
        CheckConstraint("mode IN ('emergency', 'timed', 'long')", name="mode"),
        CheckConstraint(
            "(mode = 'emergency' AND expires_at IS NULL) OR (mode IN ('timed', 'long') AND expires_at IS NOT NULL AND expires_at > created_at)",
            name="mode_expiry",
        ),
        CheckConstraint("length(public_key) = 32", name="public_key_length"),
        CheckConstraint("length(start_token_hash) = 32", name="start_token_hash_length"),
        CheckConstraint("current_version >= 1", name="version_positive"),
        CheckConstraint(
            "(owner_secret_nonce IS NULL AND owner_secret_ciphertext IS NULL AND owner_secret_data_key_id IS NULL) OR "
            "(length(owner_secret_nonce) = 12 AND length(owner_secret_ciphertext) >= 16 AND owner_secret_data_key_id IS NOT NULL)",
            name="owner_secret_shape",
        ),
        ForeignKeyConstraint(
            ["owner_secret_data_key_id", "owner_user_id"],
            ["user_data_keys.id", "user_data_keys.user_id"],
        ),
    )

    id: Mapped[str] = mapped_column(String(43), primary_key=True, default=new_share_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    mode: Mapped[str] = mapped_column(String(16))
    public_key: Mapped[bytes] = mapped_column(LargeBinary(32))
    start_token_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    owner_secret_data_key_id: Mapped[str | None] = mapped_column(String(36))
    owner_secret_nonce: Mapped[bytes | None] = mapped_column(LargeBinary(12))
    owner_secret_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)


class QrShareVersion(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "qr_share_versions"
    __table_args__ = (
        UniqueConstraint("share_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(hpke_enc) = 32", name="enc_length"),
        CheckConstraint("length(ciphertext) >= 16", name="ciphertext_length"),
    )

    share_id: Mapped[str] = mapped_column(ForeignKey("qr_shares.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    hpke_enc: Mapped[bytes] = mapped_column(LargeBinary(32))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)


class QrViewSession(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "qr_view_sessions"
    __table_args__ = (
        UniqueConstraint("qr_share_id", "attempt_hash"),
        CheckConstraint("length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("length(attempt_hash) = 32", name="attempt_hash_length"),
        CheckConstraint("absolute_expires_at > issued_at", name="absolute_expiry"),
        CheckConstraint(
            "idle_expires_at > issued_at AND idle_expires_at <= absolute_expires_at",
            name="idle_expiry",
        ),
    )

    qr_share_id: Mapped[str] = mapped_column(ForeignKey("qr_shares.id"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    attempt_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    issued_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    absolute_expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    idle_expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class DeviceMigration(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "device_migrations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('started', 'bound', 'source_approved', 'bundled', 'target_ready', 'finalized', 'cancelled', 'expired')",
            name="status",
        ),
        CheckConstraint("expires_at > created_at", name="expiry"),
        CheckConstraint("approve_failures >= 0", name="approve_failures_nonnegative"),
        CheckConstraint(
            "(bundle_enc IS NULL AND bundle_ciphertext IS NULL) OR (length(bundle_enc) = 32 AND length(bundle_ciphertext) >= 16)",
            name="bundle_shape",
        ),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_passkey_id: Mapped[str] = mapped_column(ForeignKey("passkey_credentials.id"))
    source_session_id: Mapped[str] = mapped_column(ForeignKey("auth_sessions.id"))
    status: Mapped[str] = mapped_column(String(24), default="started")
    target_public_key: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    confirmation_code: Mapped[str | None] = mapped_column(String(16))
    approve_failures: Mapped[int] = mapped_column(Integer, default=0)
    target_token_hash: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    bundle_enc: Mapped[bytes | None] = mapped_column(LargeBinary(32))
    bundle_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)
    target_passkey_id: Mapped[str | None] = mapped_column(ForeignKey("passkey_credentials.id"))
    source_approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decrypt_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    finalized_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


PROTECTION_MODELS = (
    SchemaMigration,
    UserEncryptionKey,
    UserKeyBundle,
    SessionReauth,
    RateLimitBucket,
    ShareQuotaLock,
    VerifiedContact,
    FamilyInvitation,
    FamilyConnection,
    FamilyShare,
    FamilyShareVersion,
    FamilyShareOwnerCopy,
    QrShare,
    QrShareVersion,
    QrViewSession,
    DeviceMigration,
)
