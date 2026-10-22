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
