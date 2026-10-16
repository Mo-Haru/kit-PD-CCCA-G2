"""モデル共通のID・UTC日時とSQLiteの外部キー設定。"""

from __future__ import annotations

import secrets
import sqlite3
from datetime import datetime, timezone
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, String, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.extensions import db

# 実行時は共通db.Modelそのもの。型検査では宣言的基底の型情報を使う。
if TYPE_CHECKING:
    from app.extensions import Base as Model
else:
    Model = db.Model


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


def new_share_id() -> str:
    # QRの共有ID: 256-bitのランダム値。復号鍵とは別に生成する。
    return secrets.token_urlsafe(32)


class UTCDateTime(TypeDecorator):
    """SQLiteでも読み出し時にUTCのtzinfoを復元する。naive日時は拒否。"""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime required")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


@event.listens_for(Engine, "connect")
def enable_sqlite_foreign_keys(connection, connection_record):
    if isinstance(connection, sqlite3.Connection):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()


class IdentityMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)


class CreatedMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class UpdatedMixin:
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), default=utcnow, onupdate=utcnow
    )
