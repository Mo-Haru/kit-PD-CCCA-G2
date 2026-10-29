import secrets
from datetime import timedelta

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.schema import CreateTable

from app.extensions import db
from app.models import QrShare, QrShareVersion, QrViewSession, utcnow
from app.schema_upgrade import _replace_unlimited_qr_with_long, apply_schema_upgrades
from app.services.support import b64encode
from tests.test_protection import hpke_keypair, post, qr_body, register, stamp

LONG = timedelta(days=1096)
CURRENT_MODE = "mode IN ('emergency', 'timed', 'long')"
CURRENT_EXPIRY = "(mode = 'emergency' AND expires_at IS NULL) OR (mode IN ('timed', 'long')"


def legacy_qr_table(app, *, unlimited):
    """長期QRより前のqr_shares。unlimited=Falseは無期限を足す前の形。"""
    with app.app_context():
        db.create_all()
        old = str(CreateTable(QrShare.__table__).compile(db.engine))
        assert CURRENT_MODE in old and CURRENT_EXPIRY in old
        if unlimited:
            old = old.replace(CURRENT_MODE, "mode IN ('emergency', 'timed', 'unlimited')")
            old = old.replace(CURRENT_EXPIRY, "(mode IN ('emergency', 'unlimited') AND expires_at IS NULL) OR (mode = 'timed'")
        else:
            old = old.replace(CURRENT_MODE, "mode IN ('emergency', 'timed')")
            old = old.replace(CURRENT_EXPIRY, "(mode = 'emergency' AND expires_at IS NULL) OR (mode = 'timed'")
        db.session.execute(text("DROP TABLE qr_shares"))
        db.session.execute(text(old))
        for index in QrShare.__table__.indexes:
            index.create(db.session.connection())
        db.session.commit()


def rows():
    return {model.__tablename__: db.session.execute(model.__table__.select()).all()
            for model in (QrShare, QrShareVersion, QrViewSession)}


def test_unlimited_shares_become_long_with_three_year_expiry(app):
    legacy_qr_table(app, unlimited=True)
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
        # 旧版で発行された無期限リンクを再現する。
        db.session.execute(text("UPDATE qr_shares SET mode = 'unlimited', consumed_at = NULL WHERE id = :id"),
                           {"id": body["id"]})
        db.session.commit()
        before = rows()
        applied = apply_schema_upgrades()
        assert "20261029_long_qr" in applied
        after = rows()
        assert after["qr_share_versions"] == before["qr_share_versions"]
        assert after["qr_view_sessions"] == before["qr_view_sessions"]
        share = db.session.get(QrShare, body["id"])
        assert share.mode == "long"
        assert share.expires_at == share.created_at + LONG
        assert apply_schema_upgrades() == []
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        assert {index["name"] for index in inspect(db.engine).get_indexes("qr_shares")} >= {
            index.name for index in QrShare.__table__.indexes
        }
        definition = db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='qr_shares'")).scalar()
        assert "unlimited" not in definition


def test_schema_before_unlimited_upgrades_straight_to_long(app):
    legacy_qr_table(app, unlimited=False)
    client = app.test_client()
    _, owner, _ = register(client)
    public, _ = hpke_keypair()
    body, _ = qr_body(owner["user_id"], "timed", public, secrets.token_bytes(32), b"dummy",
                      stamp(utcnow() + timedelta(days=1)))
    assert post(client, "/api/v1/qr-shares", body).status_code == 200
    with app.app_context():
        before = rows()
        applied = apply_schema_upgrades()
        assert {"20261026_unlimited_qr", "20261029_long_qr"} <= set(applied)
        assert rows() == before
        definition = db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='qr_shares'")).scalar()
        assert "'long'" in definition and "unlimited" not in definition


def test_long_qr_requires_expiry_within_three_years(app):
    with app.app_context():
        db.create_all()
    client = app.test_client()
    _, owner, _ = register(client)
    public, _ = hpke_keypair()
    now = utcnow()
    refused = [
        ("unlimited", None),
        ("long", None),
        ("long", stamp(now + LONG + timedelta(days=1))),
        ("long", stamp(now - timedelta(minutes=1))),
        ("timed", stamp(now + timedelta(days=32))),
        ("emergency", stamp(now + timedelta(days=1))),
    ]
    for mode, expires in refused:
        body, _ = qr_body(owner["user_id"], mode, public, secrets.token_bytes(32), b"dummy", expires)
        assert post(client, "/api/v1/qr-shares", body).status_code == 400, (mode, expires)
    body, _ = qr_body(owner["user_id"], "long", public, secrets.token_bytes(32), b"dummy",
                      stamp(now + LONG - timedelta(minutes=1)))
    created = post(client, "/api/v1/qr-shares", body)
    assert created.status_code == 200, created.get_json()
    assert created.get_json()["share"]["mode"] == "long"
    longer = post(client, f"/api/v1/qr-shares/{body['id']}/expiry", {
        "expected_version": 1, "expires_at": stamp(utcnow() + LONG + timedelta(days=1)),
    })
    assert longer.status_code == 400
    shorter = post(client, f"/api/v1/qr-shares/{body['id']}/expiry", {
        "expected_version": 1, "expires_at": stamp(utcnow() + timedelta(days=400)),
    })
    assert shorter.status_code == 200, shorter.get_json()


def test_long_qr_allows_repeat_views_and_closes_after_expiry(app):
    with app.app_context():
        db.create_all()
    client = app.test_client()
    _, owner, _ = register(client)
    public, _ = hpke_keypair()
    token = secrets.token_bytes(32)
    body, _ = qr_body(owner["user_id"], "long", public, token, b"dummy", stamp(utcnow() + timedelta(days=500)))
    assert post(client, "/api/v1/qr-shares", body).status_code == 200

    def start():
        return post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
            "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
        })

    assert start().status_code == 200
    assert start().status_code == 200
    with app.app_context():
        share = db.session.get(QrShare, body["id"])
        share.created_at = utcnow() - LONG
        share.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    expired = start()
    assert expired.status_code == 403
    assert expired.get_json()["error"] == "qr_expired"
    assert client.get(f"/api/v1/qr-shares/{body['id']}").get_json()["share"]["status"] == "expired"


def test_long_upgrade_failure_rolls_back_and_restores_foreign_keys(app):
    legacy_qr_table(app, unlimited=True)
    with app.app_context():
        from sqlalchemy import event
        from sqlalchemy.exc import OperationalError

        def reject_copy(connection, cursor, statement, parameters, context, many):
            if statement.startswith("INSERT INTO qr_shares ("):
                raise OperationalError(statement, parameters, RuntimeError("injected copy failure"))
        event.listen(db.engine, "before_cursor_execute", reject_copy)
        try:
            with pytest.raises(OperationalError):
                _replace_unlimited_qr_with_long()
        finally:
            event.remove(db.engine, "before_cursor_execute", reject_copy)
        definition = db.session.execute(text("SELECT sql FROM sqlite_master WHERE name='qr_shares'")).scalar()
        assert "unlimited" in definition
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert "qr_shares_keep" not in inspect(db.engine).get_table_names()
