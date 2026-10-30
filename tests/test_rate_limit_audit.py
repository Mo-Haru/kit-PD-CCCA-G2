"""回数制限で断った要求は、監査ログを要求の数だけ増やさない。"""

from datetime import timedelta

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import AuditLog, RateLimitBucket, utcnow
from tests.test_protection import post


def rate_limited_rows(app):
    with app.app_context():
        return db.session.query(AuditLog).filter_by(action="rate_limited").count()


def bucket(app):
    with app.app_context():
        return db.session.query(RateLimitBucket).one()


def test_denials_are_counted_but_logged_once_per_window(app):
    with app.app_context():
        db.create_all()
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "auth_options": (1, 60 * 60)}
    client = app.test_client()
    assert post(client, "/api/v1/auth/login/options").status_code == 200
    for _ in range(12):
        refused = post(client, "/api/v1/auth/login/options")
        assert refused.status_code == 429
        assert refused.get_json()["error"] == "rate_limited"
    # 12回断っても、監査ログは1行。断った回数はバケットに残る。
    assert rate_limited_rows(app) == 1
    assert bucket(app).denied_count == 12
    assert bucket(app).hit_count == 1
    # 次の窓では、最初に断ったときにまた1行だけ残す。
    with app.app_context():
        row = db.session.query(RateLimitBucket).one()
        row.window_started_at = utcnow() - timedelta(hours=2)
        db.session.commit()
    assert post(client, "/api/v1/auth/login/options").status_code == 200
    assert bucket(app).denied_count == 0
    for _ in range(5):
        assert post(client, "/api/v1/auth/login/options").status_code == 429
    assert rate_limited_rows(app) == 2
    assert bucket(app).denied_count == 5


def test_a_denial_does_not_commit_the_callers_pending_changes(app):
    from app.services.support import ApiError, take_rate

    with app.app_context():
        db.create_all()
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], "share_create": (0, 60 * 60)}
    with app.test_request_context(environ_base={"REMOTE_ADDR": "203.0.113.9"}):
        from app.models import User
        from app.services.support import ensure_rate_bucket

        ensure_rate_bucket("share_create", "user:x")
        db.session.add(User(display_name="途中で断られた変更"))
        try:
            take_rate("share_create", "user:x")
            raise AssertionError("expected rate_limited")
        except ApiError as error:
            assert error.code == "rate_limited"
        assert db.session.query(User).filter_by(display_name="途中で断られた変更").count() == 0


def test_upgrade_adds_the_denied_count_column(app):
    from app.schema_upgrade import apply_schema_upgrades

    with app.app_context():
        db.create_all()
        db.session.execute(text("ALTER TABLE rate_limit_buckets DROP COLUMN denied_count"))
        db.session.execute(text(
            "INSERT INTO rate_limit_buckets (bucket_key, window_started_at, hit_count) VALUES ('k', '2026-10-30 00:00:00', 3)"
        ))
        db.session.commit()
        assert "20261030_rate_limit_denied_count" in apply_schema_upgrades()
        assert "denied_count" in {column["name"] for column in inspect(db.engine).get_columns("rate_limit_buckets")}
        assert db.session.get(RateLimitBucket, "k").denied_count == 0
        assert apply_schema_upgrades() == []
