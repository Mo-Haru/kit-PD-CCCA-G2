"""QR閲覧の試行の取り消し。ページが閉じられても、遅れて届いた開始でセッションを残さない。"""

import secrets
import threading
from datetime import timedelta

from app.extensions import db
from app.models import QrShare, QrViewSession, utcnow
from app.services.support import b64encode
from config import TestingConfig
from tests.test_protection import hpke_keypair, post, qr_body, register, stamp


def issued(app, mode="emergency"):
    with app.app_context():
        db.create_all()
    owner = app.test_client()
    _passkey, created, _public = register(owner, "発行する人")
    public, _ = hpke_keypair()
    token = secrets.token_bytes(32)
    expires = stamp(utcnow() + timedelta(days=1)) if mode == "timed" else None
    body, _ = qr_body(created["user_id"], mode, public, token, b"dummy", expires)
    assert post(owner, "/api/v1/qr-shares", body).status_code == 200
    return body["id"], token


def start(client, share_id, token, attempt):
    return post(client, f"/api/v1/qr-shares/{share_id}/start",
                {"start_token": b64encode(token), "attempt_id": b64encode(attempt)})


def cancel(client, share_id, token, attempt):
    return post(client, f"/api/v1/qr-shares/{share_id}/cancel",
                {"start_token": b64encode(token), "attempt_id": b64encode(attempt)})


def live_sessions(app, share_id):
    with app.app_context():
        return db.session.query(QrViewSession).filter_by(qr_share_id=share_id, revoked_at=None).count()


def test_cancel_after_the_server_started_revokes_the_session(app):
    """開始はサーバーで完了したが、応答（Cookie）が届く前にページが閉じられた場合。"""
    share_id, token = issued(app)
    attempt = secrets.token_bytes(32)
    viewer = app.test_client()
    assert start(viewer, share_id, token, attempt).status_code == 200
    assert live_sessions(app, share_id) == 1
    # Cookieを持たない、別のクライアントからの取り消し（終了を押したページの keepalive 要求に当たる）。
    assert cancel(app.test_client(), share_id, token, attempt).get_json() == {"ok": True}
    assert live_sessions(app, share_id) == 0
    assert viewer.get("/api/v1/qr-view/current").status_code == 401


def test_cancel_before_start_blocks_the_late_start_without_consuming(app):
    share_id, token = issued(app)
    attempt = secrets.token_bytes(32)
    assert cancel(app.test_client(), share_id, token, attempt).status_code == 200
    late = start(app.test_client(), share_id, token, attempt)
    assert late.status_code == 401
    assert late.get_json()["error"] == "qr_session_expired"
    assert live_sessions(app, share_id) == 0
    with app.app_context():
        # 緊急用QRは消費していない。別の試行なら（本人が開き直せば）まだ開ける。
        assert db.session.get(QrShare, share_id).consumed_at is None
    assert start(app.test_client(), share_id, token, secrets.token_bytes(32)).status_code == 200


def test_cancel_needs_the_start_token_and_only_touches_that_attempt(app):
    share_id, token = issued(app, "timed")
    kept, other = secrets.token_bytes(32), secrets.token_bytes(32)
    viewer = app.test_client()
    assert start(viewer, share_id, token, kept).status_code == 200
    denied = cancel(app.test_client(), share_id, secrets.token_bytes(32), kept)
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "qr_denied"
    assert cancel(app.test_client(), "x" * 43, token, kept).status_code == 403
    assert cancel(app.test_client(), share_id, token, other).status_code == 200
    assert viewer.get("/api/v1/qr-view/current").status_code == 200
    # 同じ取り消しを繰り返しても同じ応答。
    assert cancel(app.test_client(), share_id, token, other).get_json() == {"ok": True}


def test_cancel_racing_start_never_leaves_a_live_session(tmp_path):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "qr.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        RATELIMIT_ENABLED = False

    app = create_app(FileConfig)
    share_id, token = issued(app, "timed")
    for _ in range(10):
        attempt = secrets.token_bytes(32)
        barrier = threading.Barrier(2)
        results = {}

        def run(name, action):
            barrier.wait()
            results[name] = action(app.test_client(), share_id, token, attempt).status_code

        threads = [threading.Thread(target=run, args=("start", start)),
                   threading.Thread(target=run, args=("cancel", cancel))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(30)
        assert results["cancel"] == 200
        assert results["start"] in {200, 401}, results
        # どちらが先でも、取り消しの後に有効なセッションは残らない。
        assert live_sessions(app, share_id) == 0, results
