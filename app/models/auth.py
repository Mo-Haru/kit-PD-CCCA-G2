"""使い捨てチャレンジと、検証済み認証に紐付くサーバー側セッション。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, JSON, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedMixin, IdentityMixin, Model, UTCDateTime, utcnow


class WebAuthnChallenge(IdentityMixin, CreatedMixin, Model):
    """検証後の消費は期限・未消費条件付きUPDATEで原子的に行う。"""

    __tablename__ = "webauthn_challenges"
    __table_args__ = (
        CheckConstraint(
            "purpose IN ('register', 'login', 'reauthenticate', 'add_passkey', 'prove_passkey')",
            name="purpose",
        ),
        CheckConstraint("length(challenge) = 32", name="challenge_length"),
        CheckConstraint("length(flow_binding_hash) = 32", name="binding_length"),
        CheckConstraint("expires_at > created_at", name="expiry"),
    )

    challenge: Mapped[bytes] = mapped_column(
        LargeBinary(32), unique=True, default=lambda: secrets.token_bytes(32)
    )
    purpose: Mapped[str] = mapped_column(String(24))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    flow_binding_hash: Mapped[bytes] = mapped_column(LargeBinary(32))
    # サーバーが生成した公開オプションのみ。PRF resultsは保存しない。
    request_options: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuthSession(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["passkey_id", "user_id"], ["passkey_credentials.id", "passkey_credentials.user_id"]
        ),
        CheckConstraint("length(token_hash) = 32", name="token_hash_length"),
        CheckConstraint("expires_at > authenticated_at", name="expiry"),
    )

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    passkey_id: Mapped[str] = mapped_column(String(36))
    # ランダム256-bit以上のセッショントークンのSHA-256。Cookieには元トークン。
    token_hash: Mapped[bytes] = mapped_column(LargeBinary(32), unique=True)
    authenticated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
