"""アプリケーション設定。

本番のデータベースや秘密情報の扱い方は未決定。
環境変数が無いときは、ローカル確認用の暫定値で起動する。
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.pool import StaticPool

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

INSTANCE_DIR = BASE_DIR / "instance"
DEFAULT_SQLITE_PATH = INSTANCE_DIR / "dev.db"
DEFAULT_DATABASE_URI = "sqlite:///" + DEFAULT_SQLITE_PATH.as_posix()


def env_or(name, default):
    """環境変数を読む。未設定か空白なら暫定値を使う。"""
    value = os.environ.get(name, "").strip()
    return value or default


class BaseConfig:
    SECRET_KEY = env_or("SECRET_KEY", "dev-change-me")
    SQLALCHEMY_DATABASE_URI = env_or("DATABASE_URL", DEFAULT_DATABASE_URI)
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    WEBAUTHN_RP_ID = env_or("WEBAUTHN_RP_ID", "localhost")
    WEBAUTHN_RP_NAME = env_or("WEBAUTHN_RP_NAME", "意思の記録")
    WEBAUTHN_ORIGIN = env_or("WEBAUTHN_ORIGIN", "http://localhost:5000")
    COOKIE_SECURE = False
    SESSION_TTL_SECONDS = 12 * 60 * 60
    REAUTH_TTL_SECONDS = 15 * 60
    CHALLENGE_TTL_SECONDS = 5 * 60
    INVITATION_TTL_SECONDS = 7 * 24 * 60 * 60
    FAMILY_SHARE_MAX_SECONDS = 366 * 24 * 60 * 60
    QR_TIMED_MAX_SECONDS = 31 * 24 * 60 * 60
    QR_VIEW_ABSOLUTE_SECONDS = 30 * 60
    QR_VIEW_IDLE_SECONDS = 5 * 60
    MIGRATION_TTL_SECONDS = 15 * 60
    MAX_CIPHERTEXT_BYTES = 256 * 1024
    MAX_SHARE_VERSIONS = 30
    RATELIMIT_ENABLED = True
    RATE_LIMITS = {
        "auth_options": (30, 10 * 60),
        "auth_verify": (20, 10 * 60),
        "invite": (20, 60 * 60),
        "qr_start": (30, 10 * 60),
        "migration": (10, 60 * 60),
    }


class DevelopmentConfig(BaseConfig):
    DEBUG = True


class TestingConfig(BaseConfig):
    TESTING = True
    SECRET_KEY = "test-only"
    WEBAUTHN_ORIGIN = "http://localhost"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {"check_same_thread": False},
        "poolclass": StaticPool,
    }


CONFIG_BY_NAME = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}


def get_config():
    """APP_ENV から設定クラスを選ぶ。本番は未決定なので起動させない。"""
    name = os.environ.get("APP_ENV", "development").strip() or "development"
    if name == "production":
        raise RuntimeError(
            "本番設定は未決定です。ローカル開発では APP_ENV=development を使ってください。"
        )
    try:
        return CONFIG_BY_NAME[name]
    except KeyError as exc:
        known = ", ".join(sorted(CONFIG_BY_NAME))
        raise RuntimeError(
            f"未対応の APP_ENV です: {name}（使える値: {known}）"
        ) from exc
