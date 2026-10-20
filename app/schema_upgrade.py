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


def _sqlite_driver(connection):
    fairy = connection.connection
    return getattr(fairy, "driver_connection", fairy)


def _end_sqlite_transaction(connection) -> None:
    """PRAGMA foreign_keys はトランザクションの中では効かない。先に同じ接続を閉じる。"""
    driver = _sqlite_driver(connection)
    if connection.in_transaction():
        connection.commit()
    if getattr(driver, "in_transaction", False):
        driver.commit()


def set_sqlite_foreign_keys(connection, enabled: bool) -> int:
    """同じ接続のトランザクション外で外部キーを切り替え、結果を読んで返す。"""
    if connection.dialect.name != "sqlite":
        return 1
    _end_sqlite_transaction(connection)
    driver = _sqlite_driver(connection)
    if getattr(driver, "in_transaction", False):
        driver.commit()
    cursor = driver.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON" if enabled else "PRAGMA foreign_keys=OFF")
        cursor.execute("PRAGMA foreign_keys")
        row = cursor.fetchone()
    finally:
        cursor.close()
    value = -1 if row is None else int(row[0])
    expected = 1 if enabled else 0
    if value != expected:
        connection.invalidate()
        raise RuntimeError("SQLiteの外部キー制約を同じ接続で戻せませんでした。")
    return value


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
    connection = db.session.connection()
    definition = connection.execute(text(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'webauthn_challenges'"
    )).scalar() or ""
    if "prove_passkey" in definition:
        return
    set_sqlite_foreign_keys(connection, False)
    connection.exec_driver_sql(
        "CREATE TABLE webauthn_challenges_keep AS "
        "SELECT challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at FROM webauthn_challenges"
    )
    connection.exec_driver_sql("DROP TABLE webauthn_challenges")
    from app.models.auth import WebAuthnChallenge
    WebAuthnChallenge.__table__.create(bind=connection)
    connection.exec_driver_sql(
        "INSERT INTO webauthn_challenges ("
        "challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at"
        ") SELECT challenge, purpose, user_id, flow_binding_hash, request_options, "
        "expires_at, consumed_at, id, created_at FROM webauthn_challenges_keep"
    )
    connection.exec_driver_sql("DROP TABLE webauthn_challenges_keep")
    if connection.in_transaction() or getattr(_sqlite_driver(connection), "in_transaction", False):
        _end_sqlite_transaction(connection)
    set_sqlite_foreign_keys(connection, True)
    # 接続上では確定済み。セッションに残った無効な取引は捨て、次の追加は新しい取引にする。
    db.session.remove()


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


def _add_passkey_proven_at() -> None:
    """既存のパスキーは確認済みとして残し、追加分だけ確認待ちにできるようにする。"""
    inspector = inspect(db.engine)
    if "passkey_credentials" not in inspector.get_table_names():
        return
    names = {column["name"] for column in inspector.get_columns("passkey_credentials")}
    if "proven_at" in names:
        return
    column_type = "TIMESTAMP" if db.engine.dialect.name == "postgresql" else "DATETIME"
    db.session.execute(text(
        f"ALTER TABLE passkey_credentials ADD COLUMN proven_at {column_type}"
    ))
    db.session.execute(text(
        "UPDATE passkey_credentials SET proven_at = created_at WHERE proven_at IS NULL"
    ))
    db.session.commit()


STEPS = (
    ("20261015_protection_tables", _create_protection_tables),
    ("20261015_migration_decrypt_confirmed", _add_migration_decrypt_confirmed),
    ("20261015_prove_passkey_purpose", _allow_prove_passkey_challenge),
    ("20261020_passkey_proven_at", _add_passkey_proven_at),
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
