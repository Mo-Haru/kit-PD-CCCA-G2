"""既存データを消さないスキーマ手順。

create_allは未作成テーブルの追加にだけ使う。既存テーブルの列変更が必要になったら、
新しい手順関数で明示的なALTERを書き、この一覧へ足す。起動時にテーブルを削除しない。
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from app.extensions import db
from app.models.protection import PROTECTION_MODELS, SchemaMigration


def _create_protection_tables() -> None:
    tables = [model.__table__ for model in PROTECTION_MODELS]
    db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)


def _allow_prove_passkey_challenge() -> None:
    """既存のチャレンジ行を残したまま、追加パスキーの確認用途を許可する。"""
    inspector = inspect(db.engine)
    if "webauthn_challenges" not in inspector.get_table_names():
        return
    if db.engine.dialect.name == "postgresql":
        definition = db.session.execute(text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conname = 'ck_webauthn_challenges_purpose'"
        )).scalar()
        if definition is None or "prove_passkey" in definition:
            return
        db.session.execute(text(
            "ALTER TABLE webauthn_challenges DROP CONSTRAINT ck_webauthn_challenges_purpose"
        ))
        db.session.execute(text(
            "ALTER TABLE webauthn_challenges ADD CONSTRAINT ck_webauthn_challenges_purpose "
            "CHECK (purpose IN ('register', 'login', 'reauthenticate', 'add_passkey', 'prove_passkey'))"
        ))
        db.session.commit()
        return
    definition = db.session.execute(text(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'webauthn_challenges'"
    )).scalar() or ""
    if "prove_passkey" in definition:
        return
    db.session.execute(text("PRAGMA foreign_keys=OFF"))
    db.session.execute(text(
        "CREATE TABLE webauthn_challenges_keep AS "
        "SELECT challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at FROM webauthn_challenges"
    ))
    db.session.execute(text("DROP TABLE webauthn_challenges"))
    db.session.commit()
    from app.models.auth import WebAuthnChallenge
    WebAuthnChallenge.__table__.create(bind=db.engine)
    db.session.execute(text(
        "INSERT INTO webauthn_challenges ("
        "challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at"
        ") SELECT challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at FROM webauthn_challenges_keep"
    ))
    db.session.execute(text("DROP TABLE webauthn_challenges_keep"))
    db.session.execute(text("PRAGMA foreign_keys=ON"))
    db.session.commit()


def _add_migration_decrypt_confirmed() -> None:
    """既存の移行行を残したまま、復号確認の時刻列だけを足す。"""
    inspector = inspect(db.engine)
    if "device_migrations" not in inspector.get_table_names():
        return
    names = {column["name"] for column in inspector.get_columns("device_migrations")}
    if "decrypt_confirmed_at" in names:
        return
    column_type = "TIMESTAMP" if db.engine.dialect.name == "postgresql" else "DATETIME"
    db.session.execute(text(
        f"ALTER TABLE device_migrations ADD COLUMN decrypt_confirmed_at {column_type}"
    ))
    db.session.commit()


STEPS = (
    ("20261015_protection_tables", _create_protection_tables),
    ("20261015_migration_decrypt_confirmed", _add_migration_decrypt_confirmed),
    ("20261015_prove_passkey_purpose", _allow_prove_passkey_challenge),
)


def apply_schema_upgrades() -> list[str]:
    """未適用の手順だけを実行し、適用した版名を返す。既存行は削除しない。"""
    SchemaMigration.__table__.create(bind=db.engine, checkfirst=True)
    applied = {row.version for row in db.session.query(SchemaMigration.version)}
    fresh: list[str] = []
    for version, step in STEPS:
        if version in applied:
            continue
        step()
        db.session.add(SchemaMigration(version=version))
        db.session.commit()
        fresh.append(version)
    return fresh
