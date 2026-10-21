"""利用者と公開鍵認証情報。秘密鍵やPRF出力は保存しない。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, JSON, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import CreatedMixin, IdentityMixin, Model, UpdatedMixin, UTCDateTime


class User(IdentityMixin, CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'suspended', 'disabled')", name="status"),
        CheckConstraint("length(webauthn_user_handle) = 32", name="handle_length"),
    )

    webauthn_user_handle: Mapped[bytes] = mapped_column(
        LargeBinary(32), unique=True, default=lambda: secrets.token_bytes(32)
    )
    # 公開してよい表示名のみ。本名等は暗号化文書に入れる。
    display_name: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), default="pending")

    passkeys: Mapped[list[PasskeyCredential]] = relationship(
        back_populates="user", passive_deletes="all"
    )


class PasskeyCredential(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "passkey_credentials"
    __table_args__ = (
        UniqueConstraint("id", "user_id"),
        CheckConstraint("sign_count >= 0", name="sign_count_nonnegative"),
        CheckConstraint("NOT backup_state OR backup_eligible", name="backup_consistency"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    # credential_id/rawIdとCOSE公開鍵をbytesで保存。
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, default=0)
    transports: Mapped[list[str]] = mapped_column(JSON, default=list)
    backup_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    backup_state: Mapped[bool] = mapped_column(Boolean, default=False)
    uv_initialized: Mapped[bool] = mapped_column(Boolean, default=False)
    prf_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    aaguid: Mapped[str | None] = mapped_column(String(36))
    label: Mapped[str | None] = mapped_column(String(100))
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # 追加パスキーは確認成功まで NULL。初回登録と移行先の登録は、その場で鍵を包むので時刻を入れる。
    proven_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    user: Mapped[User] = relationship(back_populates="passkeys")
