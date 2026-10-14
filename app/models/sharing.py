"""本人が明示的に作成する共有コピーと、アカウント限定配信のACL。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedMixin, IdentityMixin, Model, UpdatedMixin, UTCDateTime, new_share_id, utcnow


class SharedSnapshot(CreatedMixin, UpdatedMixin, Model):
    """共有内容は追記方式。公開設定以外の変更と失効取消しはサービス層で禁止。"""

    __tablename__ = "shared_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["source_document_id", "owner_user_id"], ["private_answer_documents.id", "private_answer_documents.user_id"]
        ),
        ForeignKeyConstraint(
            ["owner_data_key_id", "owner_user_id"], ["user_data_keys.id", "user_data_keys.user_id"]
        ),
        CheckConstraint("length(nonce) = 12 AND length(owner_wrap_nonce) = 12", name="nonce_lengths"),
        CheckConstraint("length(ciphertext) >= 16", name="ciphertext_length"),
        CheckConstraint("length(owner_wrapped_share_key) = 48", name="wrapped_key_length"),
        CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="expiry"),
        CheckConstraint("access_mode IN ('qr', 'recipients')", name="access_mode"),
    )

    id: Mapped[str] = mapped_column(String(43), primary_key=True, default=new_share_id)
    owner_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    source_document_id: Mapped[str] = mapped_column(String(36), index=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    crypto_version: Mapped[int] = mapped_column(Integer, default=1)
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    owner_data_key_id: Mapped[str] = mapped_column(String(36), index=True)
    # 同じ本人用鍵を使う別用途なので、クライアントで用途別HKDFを必須とする。
    # 文書用: info='acp/document/v1'
    # 再表示用: info='acp/share-owner-wrap/v1/' + snapshot_id
    # ここに保存する鍵は共有専用鍵のみ。QR再表示のために本人だけが復元する。
    owner_wrapped_share_key: Mapped[bytes] = mapped_column(LargeBinary)
    owner_wrap_nonce: Mapped[bytes] = mapped_column(LargeBinary(12))
    owner_wrap_version: Mapped[int] = mapped_column(Integer, default=1)
    access_mode: Mapped[str] = mapped_column(String(16), default="qr")
    # 確認完了後にAPIが明示的に有効化する。
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # SQLAlchemy ORM経由の設定更新でlost updateを検出。bulk UPDATEは別途条件必須。
    lock_version: Mapped[int] = mapped_column(Integer, default=1)
    __mapper_args__ = {"version_id_col": lock_version}

    def is_available(self, now: datetime | None = None) -> bool:
        """配信状態だけの判定。所有者状態/認証/共有先の認可は含まない。"""
        now = now or utcnow()
        return bool(self.is_enabled and self.revoked_at is None and (
            self.expires_at is None or now < self.expires_at
        ))


class ShareRecipient(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "share_recipients"
    __table_args__ = (UniqueConstraint("snapshot_id", "recipient_user_id"),)

    snapshot_id: Mapped[str] = mapped_column(ForeignKey("shared_snapshots.id"))
    recipient_user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # このACLは暗号鍵を配らない。相手はQR等で共有鍵を別途受領する。
    # access_mode='recipients'では、公開取得経路も必ず認証+ACLを確認する。
    # 本モデルへのINSERTだけではアクセス制限は成立しない。
