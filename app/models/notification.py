from __future__ import annotations

from datetime import datetime, time

from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, Time, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import CreatedMixin, IdentityMixin, Model, UpdatedMixin, UTCDateTime, utcnow


class ExternalAccount(IdentityMixin, Model):
    __tablename__ = "external_accounts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_user_id"),
        UniqueConstraint("user_id", "provider"),
    )

    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    provider_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class NotificationSetting(IdentityMixin, CreatedMixin, UpdatedMixin, Model):
    __tablename__ = "notification_settings"
    __table_args__ = (
        CheckConstraint("frequency IN ('daily','weekly','monthly')", name="frequency"),
        CheckConstraint(
            "weekday IS NULL OR weekday BETWEEN 0 AND 6", name="weekday_range"
        ),
        CheckConstraint(
            "day_of_month IS NULL OR day_of_month BETWEEN 1 AND 28",
            name="month_day_range",
        ),
        CheckConstraint(
            "frequency != 'weekly' OR weekday IS NOT NULL",
            name="weekly_day_required",
        ),
        CheckConstraint(
            "frequency != 'monthly' OR day_of_month IS NOT NULL",
            name="monthly_day_required",
        ),
    )

    external_account_id: Mapped[str] = mapped_column(
        ForeignKey("external_accounts.id"), nullable=False, unique=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    frequency: Mapped[str] = mapped_column(String(16), nullable=False)
    local_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Tokyo"
    )
    weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    day_of_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_scheduled_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime, nullable=True, index=True
    )


class NotificationDelivery(IdentityMixin, CreatedMixin, Model):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sending','sent','failed','cancelled')", name="status"
        ),
        CheckConstraint("attempt_count >= 0", name="attempts_nonnegative"),
        Index("ix_notification_due", "status", "scheduled_at"),
    )

    setting_id: Mapped[str] = mapped_column(
        ForeignKey("notification_settings.id"), nullable=False, index=True
    )
    scheduled_at: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deduplication_key: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    template_key: Mapped[str] = mapped_column(
        String(80), nullable=False, default="review_reminder"
    )
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
