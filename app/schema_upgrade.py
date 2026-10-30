"""既存データを消さないスキーマ手順。

create_allは未作成テーブルの追加にだけ使う。既存テーブルの列変更が必要になったら、
新しい手順関数で明示的なALTERを書き、この一覧へ足す。起動時にテーブルを削除しない。
"""

from __future__ import annotations

from datetime import timedelta

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


def _legacy_contact_rows(table: str, has_version: bool):
    if has_version:
        return db.session.execute(text(
            f"SELECT id, address_hash FROM {table} "
            "WHERE address_hash IS NOT NULL AND "
            "(address_hash_version IS NULL OR address_hash_version < 2)"
        )).all()
    return db.session.execute(text(
        f"SELECT id, address_hash FROM {table} WHERE address_hash IS NOT NULL"
    )).all()


def _migrate_contact_hashes() -> None:
    """SHA-256 の照合値を HMAC へ置き換える。旧ダイジェストは残さない。

    新方式は HMAC-SHA256(秘密, SHA-256(正規化メール))。既存行は元メールが無いので、
    HMAC-SHA256(秘密, 保存済みSHA-256) に置き換える。これは新規入力と同じ値になる。
    版が 2 の行は再実行しても変えない。秘密が無いときは SHA-256 のままにせず失敗する。
    """
    from app.contact_hash import HASH_VERSION, ContactHashKeyError, upgrade_legacy_digest

    inspector = inspect(db.engine)
    pending: list[tuple[str, str, bytes]] = []
    for table in ("verified_contacts", "family_invitations"):
        if table not in inspector.get_table_names():
            continue
        names = {column["name"] for column in inspector.get_columns(table)}
        if "address_hash" not in names:
            continue
        has_version = "address_hash_version" in names
        rows = _legacy_contact_rows(table, has_version)
        if not has_version:
            if rows:
                try:
                    upgrade_legacy_digest(rows[0][1])
                except ContactHashKeyError as exc:
                    raise RuntimeError(
                        "CONTACT_HASH_KEY が未設定です。照合用ハッシュは変換せず、SHA-256のままにはしません。"
                    ) from exc
            column = (
                "INTEGER NOT NULL DEFAULT 1"
                if table == "verified_contacts"
                else "INTEGER"
            )
            db.session.execute(text(
                f"ALTER TABLE {table} ADD COLUMN address_hash_version {column}"
            ))
            db.session.commit()
            rows = _legacy_contact_rows(table, True)
        for row_id, digest in rows:
            pending.append((table, row_id, bytes(digest)))
    if not pending:
        return
    try:
        upgraded = [(table, row_id, upgrade_legacy_digest(digest)) for table, row_id, digest in pending]
    except ContactHashKeyError as exc:
        raise RuntimeError(
            "CONTACT_HASH_KEY が未設定です。照合用ハッシュは変換せず、SHA-256のままにはしません。"
        ) from exc
    for table, row_id, digest in upgraded:
        db.session.execute(text(
            f"UPDATE {table} SET address_hash = :digest, address_hash_version = :version "
            "WHERE id = :row_id"
        ), {"digest": digest, "version": HASH_VERSION, "row_id": row_id})
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


def _qr_mode_constraint() -> str | None:
    inspector = inspect(db.engine)
    if "qr_shares" not in inspector.get_table_names():
        return None
    constraints = {row["name"]: row["sqltext"] for row in inspector.get_check_constraints("qr_shares")}
    return constraints.get("ck_qr_shares_mode", "")


def _rebuild_sqlite_qr_shares(prepare=None) -> None:
    """qr_sharesを現在のモデルで作り直す。prepareは退避表の行を新しい制約へ合わせる。"""
    from app.models import QrShare

    inspector = inspect(db.engine)
    columns = [column.name for column in QrShare.__table__.columns]
    if set(columns) != {column["name"] for column in inspector.get_columns("qr_shares")}:
        raise RuntimeError("QR共有の列構成が一致しません。既存データは変更していません。")
    names = ", ".join(f'"{name}"' for name in columns)
    connection = db.session.connection()
    set_sqlite_foreign_keys(connection, False)
    try:
        # Explicit BEGIN also makes DDL transactional in sqlite's legacy mode.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        connection.exec_driver_sql(f"CREATE TABLE qr_shares_keep AS SELECT {names} FROM qr_shares")
        if prepare is not None:
            prepare(connection)
        connection.exec_driver_sql("DROP TABLE qr_shares")
        QrShare.__table__.create(bind=connection)
        connection.exec_driver_sql(f"INSERT INTO qr_shares ({names}) SELECT {names} FROM qr_shares_keep")
        connection.exec_driver_sql("DROP TABLE qr_shares_keep")
        if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("QR共有の外部キーを保持できないため、更新を取り消します。")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        set_sqlite_foreign_keys(connection, True)
        db.session.remove()


def _allow_unlimited_qr() -> None:
    """Extend QR modes without changing issued shares, versions, or sessions."""
    constraint = _qr_mode_constraint()
    # 長期QRへ置き換え済みの表は、次の手順の形になっている。
    if constraint is None or "unlimited" in constraint or "'long'" in constraint:
        return
    if db.engine.dialect.name == "postgresql":
        for name in ("mode", "mode_expiry"):
            db.session.execute(text(f"ALTER TABLE qr_shares DROP CONSTRAINT ck_qr_shares_{name}"))
        db.session.execute(text(
            "ALTER TABLE qr_shares ADD CONSTRAINT ck_qr_shares_mode "
            "CHECK (mode IN ('emergency', 'timed', 'unlimited'))"
        ))
        db.session.execute(text(
            "ALTER TABLE qr_shares ADD CONSTRAINT ck_qr_shares_mode_expiry CHECK ("
            "(mode IN ('emergency', 'unlimited') AND expires_at IS NULL) OR "
            "(mode = 'timed' AND expires_at IS NOT NULL AND expires_at > created_at))"
        ))
        db.session.commit()
        return
    if db.engine.dialect.name != "sqlite":
        raise RuntimeError("QR共有のスキーマ更新に未対応のDBです。")
    _rebuild_sqlite_qr_shares()


def _replace_unlimited_qr_with_long() -> None:
    """無期限QRを、作成から最長期間で切れる長期QRへ置き換える。共有と閲覧セッションは残す。"""
    from flask import current_app
    from sqlalchemy import bindparam

    from app.models import QrShare

    constraint = _qr_mode_constraint()
    if constraint is None or "'long'" in constraint:
        return
    lifetime = timedelta(seconds=int(current_app.config["QR_LONG_MAX_SECONDS"]))
    if db.engine.dialect.name == "postgresql":
        for name in ("mode", "mode_expiry"):
            db.session.execute(text(f"ALTER TABLE qr_shares DROP CONSTRAINT ck_qr_shares_{name}"))
        db.session.execute(text(
            "UPDATE qr_shares SET mode = 'long', expires_at = created_at + make_interval(secs => :seconds) "
            "WHERE mode = 'unlimited'"
        ), {"seconds": int(lifetime.total_seconds())})
        db.session.execute(text(
            "ALTER TABLE qr_shares ADD CONSTRAINT ck_qr_shares_mode "
            "CHECK (mode IN ('emergency', 'timed', 'long'))"
        ))
        db.session.execute(text(
            "ALTER TABLE qr_shares ADD CONSTRAINT ck_qr_shares_mode_expiry CHECK ("
            "(mode = 'emergency' AND expires_at IS NULL) OR "
            "(mode IN ('timed', 'long') AND expires_at IS NOT NULL AND expires_at > created_at))"
        ))
        db.session.commit()
        return
    if db.engine.dialect.name != "sqlite":
        raise RuntimeError("QR共有のスキーマ更新に未対応のDBです。")
    unlimited = db.session.execute(
        QrShare.__table__.select().with_only_columns(QrShare.id, QrShare.created_at)
        .where(QrShare.mode == "unlimited")
    ).all()
    db.session.commit()
    expires_type = QrShare.__table__.c.expires_at.type
    statement = text(
        "UPDATE qr_shares_keep SET mode = 'long', expires_at = :expires WHERE id = :share_id"
    ).bindparams(bindparam("expires", type_=expires_type))

    def prepare(connection):
        for share_id, created_at in unlimited:
            connection.execute(statement, {"expires": created_at + lifetime, "share_id": share_id})

    _rebuild_sqlite_qr_shares(prepare)


def _add_invitation_confirmation() -> None:
    """招待の申請者が渡す確認リンクとコードの列を足す。既存の申請は再発行まで承認できない。"""
    inspector = inspect(db.engine)
    if "family_invitations" not in inspector.get_table_names():
        return
    names = {column["name"] for column in inspector.get_columns("family_invitations")}
    blob = "BYTEA" if db.engine.dialect.name == "postgresql" else "BLOB"
    for column, kind in (
        ("confirm_token_hash", blob),
        ("confirm_code_hash", blob),
        ("confirm_failures", "INTEGER NOT NULL DEFAULT 0"),
    ):
        if column not in names:
            db.session.execute(text(f"ALTER TABLE family_invitations ADD COLUMN {column} {kind}"))
    indexes = {index["name"] for index in inspect(db.engine).get_indexes("family_invitations")}
    if "uq_family_invitations_confirm_token" not in indexes:
        db.session.execute(text(
            "CREATE UNIQUE INDEX uq_family_invitations_confirm_token "
            "ON family_invitations (confirm_token_hash)"
        ))
    db.session.commit()


def _create_share_quota_locks() -> None:
    """共有の枠を確かめるロック行の表を足す。既存の表には触れない。"""
    from app.models import ShareQuotaLock

    ShareQuotaLock.__table__.create(bind=db.engine, checkfirst=True)


def _add_rate_limit_denied_count() -> None:
    """回数制限で断った回数の列を足す。既存のバケットは0から数える。"""
    inspector = inspect(db.engine)
    if "rate_limit_buckets" not in inspector.get_table_names():
        return
    names = {column["name"] for column in inspector.get_columns("rate_limit_buckets")}
    if "denied_count" not in names:
        db.session.execute(text("ALTER TABLE rate_limit_buckets ADD COLUMN denied_count INTEGER NOT NULL DEFAULT 0"))
        db.session.commit()


STEPS = (
    ("20261015_protection_tables", _create_protection_tables),
    ("20261015_migration_decrypt_confirmed", _add_migration_decrypt_confirmed),
    ("20261015_prove_passkey_purpose", _allow_prove_passkey_challenge),
    ("20261020_passkey_proven_at", _add_passkey_proven_at),
    ("20261021_contact_hash_hmac", _migrate_contact_hashes),
    ("20261026_unlimited_qr", _allow_unlimited_qr),
    ("20261029_share_quota_locks", _create_share_quota_locks),
    ("20261029_long_qr", _replace_unlimited_qr_with_long),
    ("20261029_family_invite_confirmation", _add_invitation_confirmation),
    ("20261030_rate_limit_denied_count", _add_rate_limit_denied_count),
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
