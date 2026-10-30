"""端末移行の取消と、移行先パスキーの登録・完了が同時に起きても、取消を上書きしない。"""

import threading

import pytest

from app.extensions import db
from app.models import DeviceMigration, PasskeyCredential
from app.services import transfer_service
from config import TestingConfig
from tests.soft_passkey import SoftPasskey
from tests.test_protection import ORIGIN, b64encode, envelope, hpke_keypair, hpke_seal, login, post, register


def file_app(tmp_path):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "transfer.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        RATELIMIT_ENABLED = False

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
    return application


def bundled_migration(app):
    """移行元の承認と鍵束の送信まで済ませ、移行先がパスキーを登録する直前の状態を作る。"""
    source = app.test_client()
    source_passkey, _owner, _public = register(source, "移行する人")
    migration_id = post(source, "/api/v1/transfer/start").get_json()["migration"]["id"]
    target = app.test_client()
    public, _private = hpke_keypair()
    bound = post(target, "/api/v1/transfer/bind", {"migration_id": migration_id, "target_public_key": b64encode(public)})
    token = bound.get_json()["transfer_token"]
    status = source.get(f"/api/v1/transfer/{migration_id}").get_json()["migration"]
    assert post(source, f"/api/v1/transfer/{migration_id}/approve", {
        "confirmation_code": bound.get_json()["migration"]["confirmation_code"],
        "fingerprint": status["fingerprint"],
    }).status_code == 200
    aad = f"hpke-v1|migration|{migration_id}|owner|target|{b64encode(public)}|1".encode()
    enc, ciphertext = hpke_seal(public, b"bundle-secret", aad)
    assert post(source, f"/api/v1/transfer/{migration_id}/bundle", {
        "hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext),
    }).status_code == 200
    options = post(target, "/api/v1/transfer/passkey/options", headers={"X-Transfer-Token": token})
    assert options.status_code == 200, options.get_json()
    target_passkey = SoftPasskey()
    verify_body = {
        "credential": target_passkey.register(options.get_json()["public_key"], ORIGIN),
        "envelope": envelope(options.get_json()["prf_input"]),
    }
    return source, source_passkey, target, token, migration_id, target_passkey, verify_body


def verify(target, token, body):
    return post(target, "/api/v1/transfer/passkey/verify", body, headers={"X-Transfer-Token": token})


class Pause:
    """関数の途中で止め、別の要求を先に確定させてから再開する。"""

    def __init__(self):
        self.reached = threading.Event()
        self.resume = threading.Event()

    def wrap(self, original):
        def paused(*args, **kwargs):
            result = original(*args, **kwargs)
            self.reached.set()
            assert self.resume.wait(30)
            return result
        return paused


def run_paused(action):
    box = {}
    thread = threading.Thread(target=lambda: box.setdefault("response", action()))
    thread.start()
    return thread, box


def active_target_passkeys(app, migration_id):
    with app.app_context():
        row = db.session.get(DeviceMigration, migration_id)
        return row.status, db.session.query(PasskeyCredential).filter_by(
            user_id=row.user_id, revoked_at=None,
        ).count()


def test_cancel_during_target_verification_wins(tmp_path, monkeypatch):
    app = file_app(tmp_path)
    source, source_passkey, target, token, migration_id, target_passkey, body = bundled_migration(app)
    pause = Pause()
    # 移行先の検証が終わった直後（状態を書き込む前）で止める。
    monkeypatch.setattr(transfer_service, "_verify_registration", pause.wrap(transfer_service._verify_registration))
    thread, box = run_paused(lambda: verify(target, token, body))
    assert pause.reached.wait(30)
    cancelled = post(source, f"/api/v1/transfer/{migration_id}/cancel")
    assert cancelled.status_code == 200
    assert cancelled.get_json()["status"] == "cancelled"
    pause.resume.set()
    thread.join(30)
    assert box["response"].status_code == 409
    assert box["response"].get_json()["error"] == "migration_state"
    # 取消が成功した後は、移行先のパスキーでログインできない。移行元は残る。
    assert login(app.test_client(), target_passkey)[0].status_code == 400
    assert login(app.test_client(), source_passkey)[0].status_code == 200
    assert active_target_passkeys(app, migration_id) == ("cancelled", 1)


def test_registration_committed_while_cancel_waits_is_revoked(tmp_path, monkeypatch):
    app = file_app(tmp_path)
    source, source_passkey, target, token, migration_id, target_passkey, body = bundled_migration(app)
    pause = Pause()
    # 取消が行を読んだ後、状態を書き込む前で止める。
    original_move = transfer_service._move

    def paused_move(*args, **kwargs):
        pause.reached.set()
        assert pause.resume.wait(30)
        return original_move(*args, **kwargs)

    monkeypatch.setattr(transfer_service, "_move", paused_move)
    thread, box = run_paused(lambda: post(source, f"/api/v1/transfer/{migration_id}/cancel"))
    assert pause.reached.wait(30)
    assert verify(target, token, body).status_code == 200
    pause.resume.set()
    thread.join(30)
    assert box["response"].status_code == 200
    assert box["response"].get_json()["status"] == "cancelled"
    # 取消の直前に確定した移行先パスキーも、取消で無効になる。
    assert login(app.test_client(), target_passkey)[0].status_code == 400
    assert login(app.test_client(), source_passkey)[0].status_code == 200
    assert active_target_passkeys(app, migration_id) == ("cancelled", 1)


@pytest.mark.parametrize("step", ["approve", "bundle"])
def test_cancelled_migration_cannot_be_advanced_by_a_late_step(tmp_path, monkeypatch, step):
    app = file_app(tmp_path)
    source = app.test_client()
    register(source, "移行する人")
    migration_id = post(source, "/api/v1/transfer/start").get_json()["migration"]["id"]
    target = app.test_client()
    public, _private = hpke_keypair()
    bound = post(target, "/api/v1/transfer/bind", {"migration_id": migration_id, "target_public_key": b64encode(public)})
    status = source.get(f"/api/v1/transfer/{migration_id}").get_json()["migration"]
    approve_body = {"confirmation_code": bound.get_json()["migration"]["confirmation_code"],
                    "fingerprint": status["fingerprint"]}
    aad = f"hpke-v1|migration|{migration_id}|owner|target|{b64encode(public)}|1".encode()
    enc, ciphertext = hpke_seal(public, b"bundle-secret", aad)
    if step == "bundle":
        assert post(source, f"/api/v1/transfer/{migration_id}/approve", approve_body).status_code == 200
        request = lambda: post(source, f"/api/v1/transfer/{migration_id}/bundle",  # noqa: E731
                               {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)})
    else:
        request = lambda: post(source, f"/api/v1/transfer/{migration_id}/approve", approve_body)  # noqa: E731
    pause = Pause()
    # 状態を確認した後（_ownedで行を読んだ後）で止め、その間に移行先から取り消す。
    monkeypatch.setattr(transfer_service, "_owned", pause.wrap(transfer_service._owned))
    thread, box = run_paused(request)
    assert pause.reached.wait(30)
    token = bound.get_json()["transfer_token"]
    assert post(target, f"/api/v1/transfer/{migration_id}/cancel", headers={"X-Transfer-Token": token}).status_code == 200
    pause.resume.set()
    thread.join(30)
    assert box["response"].status_code == 409
    with app.app_context():
        assert db.session.get(DeviceMigration, migration_id).status == "cancelled"
