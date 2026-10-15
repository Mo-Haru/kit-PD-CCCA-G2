"""APIの共通処理。秘密や平文はログへ出さない。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

from flask import current_app, jsonify, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    AuthSession,
    PasskeyCredential,
    RateLimitBucket,
    SessionReauth,
    User,
    WebAuthnChallenge,
    utcnow,
)


class ApiError(Exception):
    def __init__(self, code: str, status: int, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message
        self.details = details or {}


class ActionResult:
    def __init__(self, body: dict, status: int = 200, *, session_token: bytes | None = None,
                 clear_session: bool = False, flow_cookie: str | None = None,
                 qr_token: bytes | None = None, clear_qr: bool = False):
        self.body = body
        self.status = status
        self.session_token = session_token
        self.clear_session = clear_session
        self.flow_cookie = flow_cookie
        self.qr_token = qr_token
        self.clear_qr = clear_qr


def b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def b64decode(value, *, exact: int | None = None, minimum: int | None = None,
              maximum: int | None = None) -> bytes:
    if not isinstance(value, str) or value == "" or len(value) > 400_000:
        raise ApiError("invalid_encoding", 400, "データの形式が正しくありません。")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ApiError("invalid_encoding", 400, "データの形式が正しくありません。") from exc
    if exact is not None and len(raw) != exact:
        raise ApiError("invalid_length", 400, "データの長さが正しくありません。")
    if minimum is not None and len(raw) < minimum:
        raise ApiError("invalid_length", 400, "データの長さが正しくありません。")
    if maximum is not None and len(raw) > maximum:
        raise ApiError("too_large", 413, "送信データが大きすぎます。")
    return raw


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ApiError("invalid_time", 400, "日時はUTCのISO 8601（末尾Z）で指定してください。")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ApiError("invalid_time", 400, "日時の形式が正しくありません。") from exc
    return parsed.astimezone(timezone.utc)


def json_body(limit: int = 800_000) -> dict:
    if request.content_length and request.content_length > limit:
        raise ApiError("too_large", 413, "送信データが大きすぎます。")
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("invalid_json", 400, "JSONオブジェクトが必要です。")
    return data


def expect_fields(body: dict, allowed: set[str]) -> None:
    unknown = set(body) - allowed
    if unknown:
        raise ApiError("unexpected_field", 400, "受け付けない項目が含まれています。")


def assert_credential_safe(credential) -> None:
    if not isinstance(credential, dict):
        raise ApiError("invalid_credential", 400, "パスキー応答の形式が正しくありません。")

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                normalized = str(key).replace("-", "_").lower()
                if normalized in {"prf", "prf_output", "prf_results", "client_extension_results"}:
                    raise ApiError("secret_rejected", 400, "認証応答の拡張結果はサーバーへ送れません。")
                if normalized == "clientextensionresults" and item:
                    raise ApiError("secret_rejected", 400, "認証応答の拡張結果はサーバーへ送れません。")
                walk(item)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(credential)


def audit(action: str, target_type: str, target_id: str | None, result: str,
          error_code: str | None = None, actor_id: str | None = None) -> None:
    db.session.add(AuditLog(
        actor_user_id=actor_id,
        action=action[:80],
        target_type=target_type[:80],
        target_id=None if target_id is None else str(target_id)[:100],
        result=result,
        error_code=None if error_code is None else error_code[:80],
    ))


def fail(code: str, status: int, message: str, *, action: str | None = None,
         target_type: str = "request", target_id: str | None = None,
         actor_id: str | None = None, details: dict | None = None) -> None:
    db.session.rollback()
    if action:
        audit(action, target_type, target_id, "failure", code, actor_id)
        db.session.commit()
    raise ApiError(code, status, message, details)


def cookie_flags() -> dict:
    return {
        "secure": bool(current_app.config.get("COOKIE_SECURE")),
        "samesite": "Lax",
        "path": "/",
    }


def finish(result: ActionResult):
    response = jsonify(result.body)
    response.status_code = result.status
    flags = cookie_flags()
    if result.session_token is not None:
        response.set_cookie(
            "wn_session", b64encode(result.session_token),
            max_age=int(current_app.config["SESSION_TTL_SECONDS"]),
            httponly=True, **flags,
        )
    if result.clear_session:
        response.delete_cookie("wn_session", path="/", samesite="Lax")
    if result.flow_cookie is not None:
        response.set_cookie(
            "wn_flow", result.flow_cookie,
            max_age=int(current_app.config["CHALLENGE_TTL_SECONDS"]),
            httponly=True, **flags,
        )
    if result.qr_token is not None:
        response.set_cookie(
            "wn_qr", b64encode(result.qr_token),
            max_age=int(current_app.config["QR_VIEW_ABSOLUTE_SECONDS"]),
            httponly=True, **flags,
        )
    if result.clear_qr:
        response.delete_cookie("wn_qr", path="/", samesite="Lax")
    return response


def plant_csrf(response):
    if request.cookies.get("wn_csrf"):
        return response
    import secrets
    response.set_cookie(
        "wn_csrf", secrets.token_urlsafe(32),
        max_age=int(current_app.config["SESSION_TTL_SECONDS"]),
        httponly=False, **cookie_flags(),
    )
    return response


def tokens_match(left, right) -> bool:
    if not isinstance(left, str) or not isinstance(right, str) or len(left) != len(right):
        return False
    return hmac.compare_digest(left, right)


def enforce_rate(scope: str) -> None:
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        return
    limit, window = current_app.config["RATE_LIMITS"][scope]
    now = utcnow()
    ident = request.remote_addr or "unknown"
    key = hashlib.sha256(f"{scope}|{ident}".encode()).hexdigest()
    bucket = db.session.get(RateLimitBucket, key)
    if bucket is None:
        try:
            db.session.add(RateLimitBucket(bucket_key=key, window_started_at=now, hit_count=1))
            db.session.commit()
            return
        except IntegrityError:
            # 同時の初回リクエストは、先に確定した行を数える。
            db.session.rollback()
            bucket = db.session.get(RateLimitBucket, key)
            if bucket is None:
                fail("rate_limited", 429, "試行回数が多すぎます。しばらくしてからやり直してください。",
                     action="rate_limited", target_type="rate_limit", target_id=scope)
    if bucket.window_started_at + timedelta(seconds=window) <= now:
        bucket.window_started_at = now
        bucket.hit_count = 1
        db.session.commit()
        return
    if bucket.hit_count >= limit:
        fail("rate_limited", 429, "試行回数が多すぎます。しばらくしてからやり直してください。",
             action="rate_limited", target_type="rate_limit", target_id=scope)
    bucket.hit_count += 1
    db.session.commit()


def touch_flow() -> tuple[bytes, str | None]:
    import secrets
    raw = request.cookies.get("wn_flow")
    fresh = None
    if not raw:
        raw = secrets.token_urlsafe(32)
        fresh = raw
    return hashlib.sha256(raw.encode("ascii")).digest(), fresh


def consume_challenge(purpose: str, *, user_id: str | None = None) -> WebAuthnChallenge:
    raw = request.cookies.get("wn_flow")
    if not raw:
        fail("challenge_invalid", 400, "認証の手続きが見つかりません。最初からやり直してください。",
             action="auth_challenge", target_type="webauthn_challenge")
    flow_hash = hashlib.sha256(raw.encode("ascii")).digest()
    now = utcnow()
    query = db.session.query(WebAuthnChallenge).filter_by(
        purpose=purpose, flow_binding_hash=flow_hash, consumed_at=None,
    )
    if user_id is not None:
        query = query.filter(WebAuthnChallenge.user_id == user_id)
    else:
        query = query.filter(WebAuthnChallenge.user_id.is_(None))
    row = query.order_by(WebAuthnChallenge.created_at.desc()).first()
    if row is None:
        fail("challenge_invalid", 400, "認証の手続きが見つかりません。最初からやり直してください。",
             action="auth_challenge", target_type="webauthn_challenge")
    updated = db.session.execute(
        update(WebAuthnChallenge).where(
            WebAuthnChallenge.id == row.id,
            WebAuthnChallenge.consumed_at.is_(None),
            WebAuthnChallenge.expires_at > now,
            WebAuthnChallenge.purpose == purpose,
            WebAuthnChallenge.flow_binding_hash == flow_hash,
        ).values(consumed_at=now)
    ).rowcount
    if updated != 1:
        db.session.rollback()
        fail("challenge_invalid", 400, "認証の手続きは期限切れか、すでに使用されています。",
             action="auth_challenge", target_type="webauthn_challenge", target_id=row.id)
    db.session.commit()
    fresh = db.session.get(WebAuthnChallenge, row.id)
    if fresh is None:
        fail("challenge_invalid", 400, "認証の手続きが見つかりません。最初からやり直してください。")
    return fresh


def load_login() -> tuple[User, AuthSession, PasskeyCredential]:
    raw = request.cookies.get("wn_session")
    if not raw:
        raise ApiError("unauthenticated", 401, "ログインが必要です。")
    try:
        token = b64decode(raw, exact=32)
    except ApiError as exc:
        raise ApiError("unauthenticated", 401, "ログインが必要です。") from exc
    digest = hashlib.sha256(token).digest()
    row = db.session.query(AuthSession).filter_by(token_hash=digest).one_or_none()
    now = utcnow()
    if row is None or row.revoked_at is not None or row.expires_at <= now:
        raise ApiError("unauthenticated", 401, "ログインが必要です。")
    passkey = db.session.get(PasskeyCredential, row.passkey_id)
    user = db.session.get(User, row.user_id)
    if (passkey is None or passkey.revoked_at is not None or passkey.user_id != row.user_id
            or user is None or user.status != "active"):
        raise ApiError("unauthenticated", 401, "ログインが必要です。")
    return user, row, passkey


def require_reauth(session_row: AuthSession) -> None:
    row = db.session.get(SessionReauth, session_row.id)
    ttl = int(current_app.config["REAUTH_TTL_SECONDS"])
    if (row is None or row.passkey_id != session_row.passkey_id
            or row.reauthenticated_at + timedelta(seconds=ttl) <= utcnow()):
        raise ApiError("reauth_required", 403, "続行するには、パスキーでもう一度本人確認してください。")


def mark_reauth(session_row: AuthSession, when: datetime | None = None) -> None:
    when = when or utcnow()
    row = db.session.get(SessionReauth, session_row.id)
    if row is None:
        db.session.add(SessionReauth(
            session_id=session_row.id, passkey_id=session_row.passkey_id, reauthenticated_at=when,
        ))
    else:
        row.passkey_id = session_row.passkey_id
        row.reauthenticated_at = when


def clean_display_name(value) -> str:
    if not isinstance(value, str):
        raise ApiError("invalid_display_name", 400, "表示名は1文字以上100文字以内にしてください。")
    name = value.strip()
    if not name or len(name) > 100 or any(ord(ch) < 32 for ch in name):
        raise ApiError("invalid_display_name", 400, "表示名は1文字以上100文字以内にしてください。")
    return name


def ciphertext_limit() -> int:
    return int(current_app.config["MAX_CIPHERTEXT_BYTES"])


def commit_or_conflict(message: str = "同時に更新されたため、保存できませんでした。",
                       code: str = "conflict") -> None:
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        raise ApiError(code, 409, message) from exc
