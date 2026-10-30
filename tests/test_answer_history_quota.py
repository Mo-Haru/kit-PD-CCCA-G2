"""回答の版は、件数・容量・保存回数の上限を超えて増えない。"""

import secrets
import threading

from app.extensions import db
from app.models import PrivateAnswerDocument, RateLimitBucket, SharedSnapshot
from app.services.support import b64encode
from config import TestingConfig
from tests.test_protection import put, register

MAX_BYTES = 256 * 1024
HOUR = 60 * 60


def save(client, owner, revision, size=MAX_BYTES):
    return put(client, "/api/v1/answers/current", {
        "expected_revision": revision,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(secrets.token_bytes(size)),
        "crypto_version": 1,
        "schema_version": 1,
    })


def stored(app, user_id):
    with app.app_context():
        rows = db.session.query(PrivateAnswerDocument).filter_by(user_id=user_id).all()
        return sorted(row.revision for row in rows), sum(len(row.ciphertext) for row in rows)


def test_history_keeps_only_the_latest_versions(app, client):
    with app.app_context():
        db.create_all()
    assert app.config["ANSWER_HISTORY_KEEP"] == 20
    _passkey, owner, _public = register(client, "回答する人")
    for revision in range(25):
        saved = save(client, owner, revision)
        assert saved.status_code == 200, saved.get_json()
    revisions, total = stored(app, owner["user_id"])
    # 最大サイズで25版保存しても、残るのは最新の20版（約5MiB）だけ。
    assert revisions == list(range(6, 26))
    assert total <= 20 * MAX_BYTES
    current = client.get("/api/v1/answers/current").get_json()
    assert current["revision"] == 25
    # 版の番号は続きから増える。古い版を消しても、競合の判定は変わらない。
    assert save(client, owner, 24, size=32).status_code == 409
    assert save(client, owner, 25, size=32).status_code == 200


def test_saving_is_rate_limited_per_account(app, client):
    with app.app_context():
        db.create_all()
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "answer_save": (3, HOUR)}
    _passkey, owner, _public = register(client, "回答する人")
    for revision in range(3):
        assert save(client, owner, revision, size=32).status_code == 200
    refused = save(client, owner, 3, size=32)
    assert refused.status_code == 429
    assert "回答の保存が多すぎます" in refused.get_json()["message"]
    assert stored(app, owner["user_id"])[0] == [1, 2, 3]
    # 別のアカウントは止めない。
    other = app.test_client()
    _p2, other_body, _pub2 = register(other, "別の人")
    assert save(other, other_body, 0, size=32).status_code == 200


def test_rejected_saves_do_not_use_up_the_allowance(app, client):
    with app.app_context():
        db.create_all()
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "answer_save": (2, HOUR)}
    _passkey, owner, _public = register(client, "回答する人")
    assert save(client, owner, 0, size=32).status_code == 200
    for _ in range(5):
        assert save(client, owner, 0, size=32).status_code == 409
    assert save(client, owner, 1, size=32).status_code == 200


def test_versions_used_as_share_sources_are_kept(app, client):
    with app.app_context():
        db.create_all()
    app.config["ANSWER_HISTORY_KEEP"] = 2
    _passkey, owner, _public = register(client, "回答する人")
    assert save(client, owner, 0, size=32).status_code == 200
    with app.app_context():
        first = db.session.query(PrivateAnswerDocument).filter_by(user_id=owner["user_id"]).one()
        db.session.add(SharedSnapshot(
            owner_user_id=owner["user_id"], source_document_id=first.id, ciphertext=secrets.token_bytes(16),
            nonce=secrets.token_bytes(12), owner_data_key_id=owner["data_key_id"],
            owner_wrapped_share_key=secrets.token_bytes(48), owner_wrap_nonce=secrets.token_bytes(12),
        ))
        db.session.commit()
    for revision in range(1, 5):
        assert save(client, owner, revision, size=32).status_code == 200
    assert stored(app, owner["user_id"])[0] == [1, 4, 5]


def test_parallel_saves_store_one_version_and_count_once(tmp_path):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "answers.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}

    app = create_app(FileConfig)
    with app.app_context():
        db.create_all()
    owner_client = app.test_client()
    _passkey, owner, _public = register(owner_client, "回答する人")
    clients = []
    for _ in range(8):
        client = app.test_client()
        for name in ("wn_session", "wn_csrf"):
            client.set_cookie(name, owner_client.get_cookie(name).value)
        clients.append(client)
    barrier = threading.Barrier(len(clients))
    results = []

    def worker(client):
        barrier.wait()
        results.append(save(client, owner, 0, size=32).status_code)

    threads = [threading.Thread(target=worker, args=(client,)) for client in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    assert sorted(results) == [200] + [409] * 7, results
    assert stored(app, owner["user_id"])[0] == [1]
    with app.app_context():
        # 競合で失敗した保存は、保存回数に数えない。
        counts = [row.hit_count for row in db.session.query(RateLimitBucket).all()]
    assert 1 in counts
