"""回数制限と共有の枠は、同時に届いた要求でも上限を超えない。"""

import secrets
import threading
from datetime import timedelta

from app.extensions import db
from app.models import RateLimitBucket, utcnow
from app.services.support import enforce_rate
from config import TestingConfig
from tests.test_protection import hpke_keypair, post, qr_body, register, stamp

WORKERS = 8


def file_app(tmp_path, **settings):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "race.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}

    for name, value in settings.items():
        setattr(FileConfig, name, value)
    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
    return application


def run_together(target, count=WORKERS):
    barrier = threading.Barrier(count)
    results = []
    lock = threading.Lock()

    def worker(index):
        barrier.wait()
        outcome = target(index)
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert len(results) == count
    return results


def rate_attempt(app, scope):
    def attempt(_index):
        with app.test_request_context(environ_base={"REMOTE_ADDR": "203.0.113.7"}):
            try:
                enforce_rate(scope)
                return "allowed"
            except Exception as error:  # noqa: BLE001 - ApiErrorの429だけを数える
                return getattr(error, "code", repr(error))
            finally:
                db.session.remove()
    return attempt


def bucket(app, scope):
    import hashlib
    key = hashlib.sha256(f"{scope}|203.0.113.7".encode()).hexdigest()
    with app.app_context():
        return db.session.get(RateLimitBucket, key)


def test_parallel_hits_on_an_existing_bucket_never_exceed_the_limit(tmp_path):
    app = file_app(tmp_path)
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "register": (3, 24 * 60 * 60)}
    # 既存バケットの加算を試すため、先に1回使っておく。
    assert rate_attempt(app, "register")(0) == "allowed"
    results = run_together(rate_attempt(app, "register"))
    assert results.count("allowed") == 2, results
    assert results.count("rate_limited") == WORKERS - 2
    assert bucket(app, "register").hit_count == 3


def test_parallel_first_hits_create_one_bucket(tmp_path):
    app = file_app(tmp_path)
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "register": (3, 24 * 60 * 60)}
    results = run_together(rate_attempt(app, "register"))
    assert results.count("allowed") == 3, results
    assert bucket(app, "register").hit_count == 3


def test_parallel_hits_after_the_window_expires_reset_once(tmp_path):
    app = file_app(tmp_path)
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "register": (3, 60)}
    for _ in range(3):
        assert rate_attempt(app, "register")(0) == "allowed"
    assert rate_attempt(app, "register")(0) == "rate_limited"
    with app.app_context():
        row = bucket(app, "register")
        row = db.session.get(RateLimitBucket, row.bucket_key)
        row.window_started_at = utcnow() - timedelta(seconds=61)
        db.session.commit()
    results = run_together(rate_attempt(app, "register"))
    # 新しい窓で許可されるのは上限の3回だけ（窓の更新が複数回起きて枠が増えない）。
    assert results.count("allowed") == 3, results
    assert bucket(app, "register").hit_count == 3


def session_client(app, source):
    client = app.test_client()
    for name in ("wn_session", "wn_csrf"):
        client.set_cookie(name, source.get_cookie(name).value)
    return client


def test_parallel_share_creation_stops_at_the_active_limit(tmp_path):
    app = file_app(tmp_path, SHARE_ACTIVE_LIMIT=3)
    owner = app.test_client()
    _passkey, created, _public = register(owner, "同時に作る人")
    public, _ = hpke_keypair()

    def body():
        return qr_body(created["user_id"], "timed", public, secrets.token_bytes(32), b"dummy",
                       stamp(utcnow() + timedelta(days=1)))[0]

    for _ in range(2):
        assert post(owner, "/api/v1/qr-shares", body()).status_code == 200
    bodies = [body() for _ in range(WORKERS)]
    clients = [session_client(app, owner) for _ in range(WORKERS)]
    results = run_together(lambda index: post(clients[index], "/api/v1/qr-shares", bodies[index]).status_code)
    assert results.count(200) == 1, results
    assert results.count(409) == WORKERS - 1, results
    listed = owner.get("/api/v1/qr-shares").get_json()["shares"]
    assert len([share for share in listed if share["status"] == "active"]) == 3


def test_parallel_share_creation_respects_the_creation_rate(tmp_path):
    app = file_app(tmp_path)
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "share_create": (2, 24 * 60 * 60)}
    owner = app.test_client()
    _passkey, created, _public = register(owner, "同時に作る人")
    public, _ = hpke_keypair()
    bodies = [qr_body(created["user_id"], "emergency", public, secrets.token_bytes(32), b"dummy")[0]
              for _ in range(WORKERS)]
    clients = [session_client(app, owner) for _ in range(WORKERS)]
    results = run_together(lambda index: post(clients[index], "/api/v1/qr-shares", bodies[index]).status_code)
    assert results.count(200) == 2, results
    assert results.count(429) == WORKERS - 2, results


def test_upgrade_adds_the_quota_lock_table(app):
    from sqlalchemy import inspect, text

    from app.schema_upgrade import apply_schema_upgrades

    with app.app_context():
        db.create_all()
        db.session.execute(text("DROP TABLE share_quota_locks"))
        db.session.commit()
        assert "20261029_share_quota_locks" in apply_schema_upgrades()
        assert "share_quota_locks" in inspect(db.engine).get_table_names()
        assert apply_schema_upgrades() == []
