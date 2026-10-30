"""家族共有の停止と更新が同時に起きても、停止した共有が再び配信されない。"""

import threading

from app.extensions import db
from app.models import FamilyShare, UserDataKey
from app.services import family_service
from config import TestingConfig
from tests.test_protection import (
    b64encode, connect_pair, hpke_keypair, hpke_seal, opaque_copy, post, register, share_payload,
)


def file_app(tmp_path):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "family.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        RATELIMIT_ENABLED = False

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
    return application


def shared(app, status="active"):
    owner = app.test_client()
    _passkey, owner_body, _public = register(owner, "共有する人")
    public, _private = hpke_keypair()
    recipient, _p, recipient_body, connection_id = connect_pair(app, owner, None, "受け取る人", public)
    created, share_id, _aad = share_payload(owner, connection_id, recipient_body["user_id"],
                                            recipient_body["encryption_key_id"], public, b'{"schema":1,"items":{}}')
    assert created.status_code == 200, created.get_json()
    if status != "active":
        with app.app_context():
            db.session.get(FamilyShare, share_id).status = status
            db.session.commit()
    return owner, owner_body, recipient, recipient_body, public, share_id


def update_body(app, owner_body, recipient_body, public, share_id):
    with app.app_context():
        owner_copy = opaque_copy()
        owner_copy["data_key_id"] = db.session.query(UserDataKey).filter_by(user_id=owner_body["user_id"]).one().id
    aad = f"hpke-v1|family|{share_id}|ignored|{recipient_body['user_id']}|{recipient_body['encryption_key_id']}|2".encode()
    enc, ciphertext = hpke_seal(public, b'{"schema":1,"items":{"q1":{"state":"undecided"}}}', aad)
    return {
        "expected_version": 1,
        "recipient_key_id": recipient_body["encryption_key_id"],
        "confirmed": True,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
        "owner_copy": owner_copy,
    }


def paused_call(monkeypatch, name):
    """family_service.<name> の呼び出しで止める。止めている間に別の要求を確定させる。"""
    reached, resume = threading.Event(), threading.Event()
    original = getattr(family_service, name)

    def paused(*args, **kwargs):
        reached.set()
        assert resume.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(family_service, name, paused)
    return reached, resume


def in_thread(action):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("response", action()))
    thread.start()
    return thread, box


def session_client(app, source):
    client = app.test_client()
    for name in ("wn_session", "wn_csrf"):
        client.set_cookie(name, source.get_cookie(name).value)
    return client


def test_stop_during_an_update_is_not_overwritten(tmp_path, monkeypatch):
    for status in ("key_pending", "active"):
        (tmp_path / status).mkdir()
        app = file_app(tmp_path / status)
        owner, owner_body, recipient, recipient_body, public, share_id = shared(app, status)
        body = update_body(app, owner_body, recipient_body, public, share_id)
        # 更新が状態を確かめた後（相手の鍵を調べる所）で止め、その間に停止を確定させる。
        reached, resume = paused_call(monkeypatch, "_active_key")
        thread, box = in_thread(lambda: post(session_client(app, owner), f"/api/v1/family/shares/{share_id}/update", body))
        assert reached.wait(30)
        monkeypatch.undo()
        assert post(owner, f"/api/v1/family/shares/{share_id}/stop").get_json()["status"] == "stopped"
        resume.set()
        thread.join(30)
        assert box["response"].status_code == 409, (status, box["response"].get_json())
        assert box["response"].get_json()["error"] == "share_stopped"
        with app.app_context():
            row = db.session.get(FamilyShare, share_id)
            assert (row.status, row.current_version) == ("stopped", 1)
            assert row.stopped_at is not None
        assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404


def test_update_committed_before_stop_is_still_stopped(tmp_path, monkeypatch):
    app = file_app(tmp_path)
    owner, owner_body, recipient, recipient_body, public, share_id = shared(app, "key_pending")
    body = update_body(app, owner_body, recipient_body, public, share_id)
    # 停止が共有を読んだ後で止め、その間に更新を確定させる。
    reached, resume = paused_call(monkeypatch, "require_reauth")
    thread, box = in_thread(lambda: post(session_client(app, owner), f"/api/v1/family/shares/{share_id}/stop"))
    assert reached.wait(30)
    monkeypatch.undo()
    assert post(owner, f"/api/v1/family/shares/{share_id}/update", body).status_code == 200
    resume.set()
    thread.join(30)
    assert box["response"].get_json()["status"] == "stopped"
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404


def test_a_share_with_a_stop_record_is_never_delivered(tmp_path):
    app = file_app(tmp_path)
    _owner, _owner_body, recipient, _recipient_body, _public, share_id = shared(app)
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 200
    with app.app_context():
        from app.models import utcnow
        # 状態の値が active のままでも、停止の記録があれば配信しない。
        db.session.get(FamilyShare, share_id).stopped_at = utcnow()
        db.session.commit()
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404
    listed = recipient.get("/api/v1/family/received").get_json()["shares"]
    assert all("current" not in share or share["current"] is None for share in listed if share["id"] == share_id)
