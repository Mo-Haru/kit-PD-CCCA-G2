import runpy
from pathlib import Path

import pytest

from app import create_app
from config import DEFAULT_DATABASE_URI, DevelopmentConfig


def test_env_or_ignores_blank_values(monkeypatch):
    from config import env_or

    monkeypatch.setenv("DATABASE_URL", "   ")
    assert env_or("DATABASE_URL", "sqlite:///fallback") == "sqlite:///fallback"
    monkeypatch.setenv("DATABASE_URL", "sqlite:///custom.db")
    assert env_or("DATABASE_URL", "sqlite:///fallback") == "sqlite:///custom.db"


def test_blank_contact_hash_key_is_not_replaced(monkeypatch):
    from config import contact_hash_key_from_environ

    monkeypatch.delenv("CONTACT_HASH_KEY", raising=False)
    assert contact_hash_key_from_environ() is None
    monkeypatch.setenv("CONTACT_HASH_KEY", "   ")
    assert contact_hash_key_from_environ() is None
    monkeypatch.setenv("CONTACT_HASH_KEY", "x" * 32)
    assert contact_hash_key_from_environ() == "x" * 32


def test_default_database_is_local_sqlite():
    assert DEFAULT_DATABASE_URI.startswith("sqlite:///")
    assert DEFAULT_DATABASE_URI.endswith("instance/dev.db")


@pytest.mark.parametrize("configured_origin,browser_origin,expected_status", [
    (None, "http://localhost:5000", 200),
    (None, "http://localhost:5001", 400),
    ("http://localhost:5001", "http://localhost:5001", 200),
    ("http://localhost:5001", "http://localhost:5000", 400),
])
def test_development_passkey_origin_without_dotenv(monkeypatch, configured_origin, browser_origin, expected_status):
    from app.extensions import db
    from config import TestingConfig
    from tests.soft_passkey import SoftPasskey
    from app.services.support import b64encode
    from tests.test_protection import bundle, envelope, hpke_keypair, post

    # Evaluate the real development defaults without reading the workspace's .env.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *_args, **_kwargs: False)
    monkeypatch.delenv("WEBAUTHN_RP_ID", raising=False)
    monkeypatch.delenv("WEBAUTHN_ORIGIN", raising=False)
    if configured_origin is not None:
        monkeypatch.setenv("WEBAUTHN_ORIGIN", configured_origin)
    development = runpy.run_path(str(Path(__file__).resolve().parents[1] / "config.py"))["DevelopmentConfig"]
    assert development.WEBAUTHN_ORIGIN == (configured_origin or "http://localhost:5000")
    memory_config = type("MemoryDevelopment", (development,), {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "SQLALCHEMY_ENGINE_OPTIONS": TestingConfig.SQLALCHEMY_ENGINE_OPTIONS,
    })
    application = create_app(memory_config)
    with application.app_context():
        db.create_all()
        try:
            client = application.test_client()
            options = post(client, "/api/v1/auth/register/options", {"display_name": "テスト利用者"})
            assert options.status_code == 200
            payload = options.get_json()
            public_key, _private = hpke_keypair()
            response = post(client, "/api/v1/auth/register/verify", {
                "credential": SoftPasskey().register(payload["public_key"], browser_origin),
                "envelope": envelope(payload["prf_input"]),
                "encryption_public_key": b64encode(public_key),
                "key_bundle": bundle(),
            })
            assert response.status_code == expected_status, response.get_json()
            if expected_status == 400:
                assert response.get_json()["error"] == "webauthn_invalid"
        finally:
            db.session.remove()
            db.drop_all()
            db.engine.dispose()


def test_development_env_uses_development_config(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    from config import get_config

    assert get_config() is DevelopmentConfig


def test_production_is_not_configured(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(RuntimeError, match="本番設定は未決定"):
        create_app()


def test_unknown_app_env_is_rejected(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    with pytest.raises(RuntimeError, match="未対応の APP_ENV"):
        create_app()
