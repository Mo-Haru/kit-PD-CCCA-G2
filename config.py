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


def contact_hash_key_from_environ():
    """連絡先照合の秘密。未設定や空白は None であり、弱い既定値へ落とさない。"""
    value = os.environ.get("CONTACT_HASH_KEY", "").strip()
    return value or None


def proxy_count_from_environ() -> int:
    """手前のプロキシの段数。未設定・不正値は0（転送ヘッダーを信用しない）。"""
    value = os.environ.get("TRUSTED_PROXY_COUNT", "").strip()
    if not value:
        return 0
    try:
        count = int(value)
    except ValueError as exc:
        raise RuntimeError("TRUSTED_PROXY_COUNT は0以上の整数で指定してください。") from exc
    if count < 0 or count > 5:
        raise RuntimeError("TRUSTED_PROXY_COUNT は0〜5で指定してください。")
    return count


class BaseConfig:
    SECRET_KEY = env_or("SECRET_KEY", "dev-change-me")
    # メール照合用。未設定なら None。開発用の固定秘密にはしない。
    CONTACT_HASH_KEY = contact_hash_key_from_environ()
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
        # 新規登録は検証に成功した分だけ数える。接続元ごとと、サイト全体の両方で止める。
        "register": (5, 24 * 60 * 60),
        "register_global": (100, 60 * 60),
        # 共有（QR・家族）の新規作成。アカウントごとに数え、作成と停止の繰り返しも抑える。
        "share_create": (60, 24 * 60 * 60),
    }
    # 1アカウントが同時に持てる有効な共有（QRと家族共有の合計）。停止・期限切れ・使用済みは数えない。
    SHARE_ACTIVE_LIMIT = 20
    # アプリの手前にあるリバースプロキシの段数。0ならX-Forwarded-Forを信用しない。
    TRUSTED_PROXY_COUNT = proxy_count_from_environ()


class DevelopmentConfig(BaseConfig):
    DEBUG = True
    DEMO_QUESTIONS_ENABLED = True


class TestingConfig(BaseConfig):
    TESTING = True
    DEMO_QUESTIONS_ENABLED = True
    SECRET_KEY = "test-only"
    CONTACT_HASH_KEY = "test-only-contact-hash-key-32b!!"
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
