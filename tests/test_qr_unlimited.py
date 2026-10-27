import secrets
from datetime import timedelta

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable

from app.extensions import db
from app.models import QrShare, QrShareVersion, QrViewSession, utcnow
from app.schema_upgrade import _allow_unlimited_qr, apply_schema_upgrades
from app.services.support import b64encode
from tests.test_protection import hpke_keypair, post, qr_body, register, stamp


def legacy_qr_table(app):
    with app.app_context():
        db.create_all()
        old = str(CreateTable(QrShare.__table__).compile(db.engine))
        old = old.replace("'emergency', 'timed', 'unlimited'", "'emergency', 'timed'")
        old = old.replace("mode IN ('emergency', 'unlimited')", "mode = 'emergency'")
        db.session.execute(text("DROP TABLE qr_shares"))
        db.session.execute(text(old))
        for index in QrShare.__table__.indexes:
            index.create(db.session.connection())
        db.session.commit()


def test_unlimited_upgrade_preserves_issued_shares_versions_and_sessions(app):
    legacy_qr_table(app)
    client = app.test_client()
    _, owner, _ = register(client)
    public, _ = hpke_keypair()
    token = secrets.token_bytes(32)
    body, _ = qr_body(owner["user_id"], "emergency", public, token, b"migration-dummy")
    assert post(client, "/api/v1/qr-shares", body).status_code == 200
    assert post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    }).status_code == 200
    with app.app_context():
        def rows():
            return {model.__tablename__: db.session.execute(model.__table__.select()).all()
                    for model in (QrShare, QrShareVersion, QrViewSession)}
        before = rows()
        assert "20261026_unlimited_qr" in apply_schema_upgrades()
        assert rows() == before
        assert apply_schema_upgrades() == []
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert {index["name"] for index in inspect(db.engine).get_indexes("qr_shares")} >= {
            index.name for index in QrShare.__table__.indexes
        }
    new, _ = qr_body(owner["user_id"], "unlimited", public, secrets.token_bytes(32), b"unlimited-dummy")
    assert post(client, "/api/v1/qr-shares", new).status_code == 200


def test_unlimited_rejects_expiry_and_timed_still_requires_expiry(app):
    with app.app_context():
        db.create_all()
    client = app.test_client()
    _, owner, _ = register(client)
    public, _ = hpke_keypair()
    for mode, expires in (("unlimited", stamp(utcnow() + timedelta(days=1))), ("timed", None)):
        body, _ = qr_body(owner["user_id"], mode, public, secrets.token_bytes(32), b"dummy", expires)
        assert post(client, "/api/v1/qr-shares", body).status_code == 400


def test_upgrade_failure_rolls_back_and_restores_foreign_keys(app, monkeypatch):
    legacy_qr_table(app)
    with app.app_context():
        from sqlalchemy import event
        from sqlalchemy.exc import OperationalError

        def reject_copy(connection, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO qr_shares ("):
                raise OperationalError(statement, parameters, RuntimeError("injected copy failure"))
        event.listen(db.engine, "before_cursor_execute", reject_copy)
        try:
            with pytest.raises(OperationalError):
                _allow_unlimited_qr()
        finally:
            event.remove(db.engine, "before_cursor_execute", reject_copy)
        definition = db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='qr_shares'")).scalar()
        assert "unlimited" not in definition
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert "qr_shares_keep" not in inspect(db.engine).get_table_names()
