"""機密内容を含めない操作記録。"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedMixin, IdentityMixin, Model


class AuditLog(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "audit_logs"
    __table_args__ = (
        CheckConstraint("result IN ('success', 'failure')", name="result"),
        Index("ix_audit_target", "target_type", "target_id", "created_at"),
    )

    actor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(80))
    target_type: Mapped[str] = mapped_column(String(80))
    # 削除済み対象も記録するため意図的に外部キーにしない。
    target_id: Mapped[str | None] = mapped_column(String(100))
    result: Mapped[str] = mapped_column(String(16))
    # 自由文のexceptionやリクエスト本文は入れず、固定エラーコードのみ。
    error_code: Mapped[str | None] = mapped_column(String(80))
