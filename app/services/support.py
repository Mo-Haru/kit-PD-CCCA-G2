"""APIの共通処理。秘密や平文はログへ出さない。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
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


def json_body() -> dict:
    """JSONオブジェクトの本文を読む。上限はアプリ全体の MAX_CONTENT_LENGTH。

    Content-Lengthがあれば、本文を読む前に断る。chunkedなどContent-Lengthのない本文は、
    Werkzeugが上限のバイト数で読み込みを止める（例外は出さずに切り詰める）。
    そこで上限まで読んだ後、元の入力にまだ続きがあれば超過として断る。解析するのは上限以内の本文だけ。
    """
    limit = int(current_app.config["MAX_CONTENT_LENGTH"])
    if request.content_length and request.content_length > limit:
        raise ApiError("too_large", 413, "送信データが大きすぎます。")
    raw = request.get_data(cache=True)
    if request.content_length is None and len(raw) >= limit and request.environ["wsgi.input"].read(1):
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


RATE_MESSAGE = "試行回数が多すぎます。しばらくしてからやり直してください。"


def client_key() -> str:
    """回数制限で数える接続元。IPv6は回線単位で配られる/64にまとめ、付け替えで逃げられないようにする。

    リバースプロキシ越しの接続元は、TRUSTED_PROXY_COUNT を設定したときだけ ProxyFix が remote_addr に入れる。
    """
    try:
        address = ipaddress.ip_address(request.remote_addr or "")
    except ValueError:
        return "unknown"
    if address.version == 6:
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.ip_network(f"{address}/64", strict=False))
    return str(address)


def _rate_bucket(scope: str, ident: str | None) -> tuple[str, int, int]:
    limit, window = current_app.config["RATE_LIMITS"][scope]
    ident = client_key() if ident is None else ident
    return hashlib.sha256(f"{scope}|{ident}".encode()).hexdigest(), int(limit), int(window)


def ensure_rate_bucket(scope: str, ident: str | None = None) -> None:
    """バケット行が無ければ作る。この中でコミットするので、ロックを取る前に呼ぶ。"""
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        return
    key, _limit, _window = _rate_bucket(scope, ident)
    if db.session.get(RateLimitBucket, key) is not None:
        return
    try:
        # 窓は切れた状態で作り、最初の1回も take_rate の窓の更新で数える。
        db.session.add(RateLimitBucket(bucket_key=key, window_started_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
                                       hit_count=0))
        db.session.commit()
    except IntegrityError:
        # 同時の初回リクエストは、先に作られた行を使う。
        db.session.rollback()


def take_rate(scope: str, ident: str | None = None, message: str = RATE_MESSAGE) -> None:
    """確認と加算をDB上の条件付きUPDATE1回で行う。コミットは呼び出し側。超えたらロールバックして断る。

    読み取ってからPythonで足すと、同時の要求が同じ値を書いて回数が失われるため。
    """
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        return
    key, limit, window = _rate_bucket(scope, ident)
    now = utcnow()
    start = now - timedelta(seconds=window)
    table = RateLimitBucket
    taken = 0
    if limit > 0:
        # 窓が切れていれば、この要求を1回目として窓を作り直す。同時に来ても条件に合うのは1件だけ。
        taken = db.session.execute(
            update(table).where(table.bucket_key == key, table.window_started_at <= start)
            .values(window_started_at=now, hit_count=1, denied_count=0)
        ).rowcount
        if not taken:
            taken = db.session.execute(
                update(table).where(table.bucket_key == key, table.window_started_at > start,
                                    table.hit_count < limit)
                .values(hit_count=table.hit_count + 1)
            ).rowcount
    if taken != 1:
        _deny_rate(scope, key, now, start, message)


def _deny_rate(scope: str, key: str, now, start, message: str) -> None:
    """断った回数をバケットに数え、監査ログは窓ごとに最初の1回だけ残す。

    断るたびに監査ログを1行足すと、上限に達した後も小さな要求を送り続けるだけでDBを増やせるため。
    どの要求が「窓の最初の拒否」かは、断った回数を0から1へ変えた条件付きUPDATEの件数で決める
    （数を読んで判断すると、期限切れの窓や同時の拒否で取り違える）。
    呼び出し側の途中の変更は残さない（先にロールバックしてから、数えた結果だけをコミットする）。
    """
    db.session.rollback()
    bucket = RateLimitBucket
    # 窓が切れていれば、この拒否を新しい窓の最初の拒否として窓を作り直す（上限0では許可で作り直されないため）。
    first = db.session.execute(
        update(bucket).where(bucket.bucket_key == key, bucket.window_started_at <= start)
        .values(window_started_at=now, hit_count=0, denied_count=1)
    ).rowcount == 1
    if not first:
        first = db.session.execute(
            update(bucket).where(bucket.bucket_key == key, bucket.window_started_at > start, bucket.denied_count == 0)
            .values(denied_count=1)
        ).rowcount == 1
    if not first:
        db.session.execute(
            update(bucket).where(bucket.bucket_key == key, bucket.window_started_at > start)
            .values(denied_count=bucket.denied_count + 1)
        )
    if first:
        audit("rate_limited", "rate_limit", scope, "failure", "rate_limited")
    db.session.commit()
    raise ApiError("rate_limited", 429, message)


def enforce_rate(scope: str, ident: str | None = None, message: str = RATE_MESSAGE) -> None:
    """scopeごとに回数を数える。identを省くと接続元、"all"ならサイト全体、"user:<id>"ならアカウント単位。"""
    if not current_app.config.get("RATELIMIT_ENABLED", True):
        return
    ensure_rate_bucket(scope, ident)
    take_rate(scope, ident, message)
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
