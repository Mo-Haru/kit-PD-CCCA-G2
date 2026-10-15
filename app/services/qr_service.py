"""QR共有のAPI。画像の生成と表示はしない。秘密鍵と完全なURLは返さない。"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import timedelta

from flask import current_app, request
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import QrShare, QrShareVersion, QrViewSession, UserDataKey, utcnow
from app.models.base import new_share_id
from app.services.support import (
    ActionResult,
    audit,
    b64decode,
    b64encode,
    ciphertext_limit,
    expect_fields,
    fail,
    iso,
    load_login,
    parse_iso,
    require_reauth,
)

SHARE_ID = re.compile(r"^[A-Za-z0-9_-]{43}$")
CREATE_FIELDS = {"id", "mode", "expires_at", "public_key", "start_token_hash", "version", "owner_secret"}
UPDATE_FIELDS = {"expected_version", "version"}
EXPIRY_FIELDS = {"expected_version", "expires_at"}


def _share_id(value) -> str:
    if not isinstance(value, str) or SHARE_ID.fullmatch(value) is None:
        fail("invalid_id", 400, "共有IDが正しくありません。")
    return value


def _hpke(version) -> tuple[bytes, bytes]:
    if not isinstance(version, dict):
        fail("invalid_ciphertext", 400, "共有データの形式が正しくありません。")
    expect_fields(version, {"hpke_enc", "ciphertext"})
    return (
        b64decode(version.get("hpke_enc"), exact=32),
        b64decode(version.get("ciphertext"), minimum=16, maximum=ciphertext_limit()),
    )


def _owner_secret(body: dict, owner_id: str):
    secret = body.get("owner_secret")
    if secret is None:
        return None, None, None
    if not isinstance(secret, dict):
        fail("invalid_ciphertext", 400, "所有者用の秘密情報の形式が正しくありません。")
    expect_fields(secret, {"data_key_id", "nonce", "ciphertext"})
    data_key = db.session.get(UserDataKey, secret.get("data_key_id"))
    if data_key is None or data_key.user_id != owner_id or data_key.retired_at is not None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。")
    return (
        data_key.id,
        b64decode(secret.get("nonce"), exact=12),
        b64decode(secret.get("ciphertext"), minimum=16, maximum=ciphertext_limit()),
    )


def _view(share: QrShare, version: QrShareVersion | None = None) -> dict:
    current = version
    if current is None:
        current = db.session.query(QrShareVersion).filter_by(
            share_id=share.id, version=share.current_version,
        ).one()
    return {
        "id": share.id,
        "owner_user_id": share.owner_user_id,
        "mode": share.mode,
        "status": _status(share),
        "expires_at": iso(share.expires_at),
        "stopped_at": iso(share.stopped_at),
        "consumed_at": iso(share.consumed_at),
        "current_version": share.current_version,
        "public_key": b64encode(share.public_key),
        "created_at": iso(share.created_at),
        "updated_at": iso(share.updated_at),
        "version": {
            "version": current.version,
            "hpke_enc": b64encode(current.hpke_enc),
            "ciphertext": b64encode(current.ciphertext),
            "created_at": iso(current.created_at),
        },
    }


def _status(share: QrShare, now=None) -> str:
    now = now or utcnow()
    if share.stopped_at is not None:
        return "stopped"
    if share.mode == "timed" and (share.expires_at is None or share.expires_at <= now):
        return "expired"
    if share.mode == "emergency" and share.consumed_at is not None:
        return "used"
    return "active"


def create_share(body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, CREATE_FIELDS)
    mode = body.get("mode")
    now = utcnow()
    if mode == "emergency":
        if body.get("expires_at") is not None:
            fail("invalid_time", 400, "緊急用QRに期限は設定できません。", actor_id=user.id)
        expires = None
    elif mode == "timed":
        expires = parse_iso(body.get("expires_at"))
        latest = now + timedelta(seconds=int(current_app.config["QR_TIMED_MAX_SECONDS"]))
        if expires <= now or expires > latest:
            fail("invalid_time", 400, "期限付きQRの期限が範囲外です。", actor_id=user.id)
    else:
        fail("invalid_mode", 400, "QRの種類が正しくありません。", actor_id=user.id)
    share_id = _share_id(body.get("id") or new_share_id())
    public_key = b64decode(body.get("public_key"), exact=32)
    start_hash = b64decode(body.get("start_token_hash"), exact=32)
    enc, ciphertext = _hpke(body.get("version"))
    secret_key_id, secret_nonce, secret_ct = _owner_secret(body, user.id)
    share = QrShare(
        id=share_id,
        owner_user_id=user.id,
        mode=mode,
        public_key=public_key,
        start_token_hash=start_hash,
        expires_at=expires,
        current_version=1,
        created_at=now,
        owner_secret_data_key_id=secret_key_id,
        owner_secret_nonce=secret_nonce,
        owner_secret_ciphertext=secret_ct,
    )
    db.session.add(share)
    version = QrShareVersion(share_id=share.id, version=1, hpke_enc=enc, ciphertext=ciphertext)
    db.session.add(version)
    audit("qr_created", "qr_share", share.id, "success", actor_id=user.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        fail("conflict", 409, "QR共有を作成できませんでした。")
        raise exc
    return {"share": _view(share, version)}


def list_shares() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(QrShare).filter_by(owner_user_id=user.id).order_by(QrShare.created_at.desc()).limit(100).all()
    return {"shares": [_view(row) for row in rows]}


def get_share(share_id: str) -> dict:
    user, _session, _passkey = load_login()
    share = _owned(share_id, user.id)
    return {"share": _view(share)}


def _owned(share_id: str, user_id: str) -> QrShare:
    share = db.session.get(QrShare, _share_id(share_id))
    if share is None or share.owner_user_id != user_id:
        fail("not_found", 404, "QR共有が見つかりません。")
    return share


def update_share(share_id: str, body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, UPDATE_FIELDS)
    share = _owned(share_id, user.id)
    now = utcnow()
    if share.stopped_at is not None:
        fail("qr_stopped", 409, "停止したQRは更新できません。", actor_id=user.id)
    if share.mode == "timed" and (share.expires_at is None or share.expires_at <= now):
        fail("qr_expired", 409, "期限が切れたQRは更新できません。", actor_id=user.id)
    if body.get("expected_version") != share.current_version:
        fail("revision_conflict", 409, "別の版があるため更新できません。", actor_id=user.id)
    if share.current_version >= int(current_app.config["MAX_SHARE_VERSIONS"]):
        fail("history_limit", 400, "この共有の履歴が上限に達しています。", actor_id=user.id)
    version_no = share.current_version + 1
    enc, ciphertext = _hpke(body.get("version"))
    share.current_version = version_no
    row = QrShareVersion(share_id=share.id, version=version_no, hpke_enc=enc, ciphertext=ciphertext)
    db.session.add(row)
    audit("qr_updated", "qr_share", share.id, "success", actor_id=user.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        fail("revision_conflict", 409, "別の版があるため更新できません。")
        raise exc
    return {"share": _view(share, row)}


def stop_share(share_id: str) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    share = _owned(share_id, user.id)
    if share.stopped_at is None:
        share.stopped_at = utcnow()
        audit("qr_stopped", "qr_share", share.id, "success", actor_id=user.id)
        db.session.commit()
    return {"ok": True, "status": _status(share)}


def change_expiry(share_id: str, body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, EXPIRY_FIELDS)
    share = _owned(share_id, user.id)
    if share.mode != "timed":
        fail("invalid_mode", 400, "緊急用QRの期限は変更できません。", actor_id=user.id)
    if body.get("expected_version") != share.current_version:
        fail("revision_conflict", 409, "別の版があるため変更できません。", actor_id=user.id)
    expires = parse_iso(body.get("expires_at"))
    if expires <= share.created_at:
        fail("invalid_time", 400, "期限は作成時刻より後にしてください。", actor_id=user.id)
    latest = utcnow() + timedelta(seconds=int(current_app.config["QR_TIMED_MAX_SECONDS"]))
    if expires > latest:
        fail("invalid_time", 400, "期限付きQRの期限が範囲外です。", actor_id=user.id)
    share.expires_at = expires
    audit("qr_expiry_changed", "qr_share", share.id, "success", actor_id=user.id)
    db.session.commit()
    return {"share": _view(share)}


def _session_from_cookie() -> QrViewSession:
    raw = request.cookies.get("wn_qr")
    if not raw:
        fail("qr_unauthenticated", 401, "閲覧セッションがありません。")
    try:
        token = b64decode(raw, exact=32)
    except Exception:
        fail("qr_unauthenticated", 401, "閲覧セッションがありません。")
    digest = hashlib.sha256(token).digest()
    row = db.session.query(QrViewSession).filter_by(token_hash=digest).one_or_none()
    if row is None or row.revoked_at is not None:
        fail("qr_unauthenticated", 401, "閲覧セッションがありません。")
    return row


def _reject_closed_share(share: QrShare, now) -> None:
    if share.stopped_at is not None:
        fail("qr_stopped", 403, "このQRは停止されています。",
             action="qr_start", target_type="qr_share", target_id=share.id)
    if share.mode == "timed" and (share.expires_at is None or share.expires_at <= now):
        fail("qr_expired", 403, "このQRの期限が切れています。",
             action="qr_start", target_type="qr_share", target_id=share.id)


def _resume_attempt(share: QrShare, attempt_hash: bytes, now):
    """同じ試行の再送。無操作期限や絶対期限が切れたセッションは延ばさない。"""
    existing = db.session.query(QrViewSession).filter_by(
        qr_share_id=share.id, attempt_hash=attempt_hash,
    ).one_or_none()
    if existing is None:
        return None
    if (existing.revoked_at is not None or existing.absolute_expires_at <= now
            or existing.idle_expires_at <= now):
        fail("qr_session_expired", 401, "閲覧セッションの期限が切れています。同じ手続きでは再開できません。",
             action="qr_start", target_type="qr_view_session", target_id=existing.id)
    token = secrets.token_bytes(32)
    existing.token_hash = hashlib.sha256(token).digest()
    idle = now + timedelta(seconds=int(current_app.config["QR_VIEW_IDLE_SECONDS"]))
    existing.idle_expires_at = idle if idle < existing.absolute_expires_at else existing.absolute_expires_at
    db.session.commit()
    return existing, token


def start_view(share_id: str, body: dict) -> ActionResult:
    from app.services.support import enforce_rate
    enforce_rate("qr_start")
    expect_fields(body, {"start_token", "attempt_id"})
    share_key = share_id if isinstance(share_id, str) else ""
    if SHARE_ID.fullmatch(share_key or "") is None:
        fail("qr_denied", 403, "このQRでは閲覧を開始できません。", action="qr_start", target_type="qr_share")
    try:
        token = b64decode(body.get("start_token"), exact=32)
        attempt = b64decode(body.get("attempt_id"), minimum=16, maximum=64)
    except Exception:
        fail("qr_denied", 403, "このQRでは閲覧を開始できません。", action="qr_start", target_type="qr_share")
    token_hash = hashlib.sha256(token).digest()
    attempt_hash = hashlib.sha256(attempt).digest()
    share = db.session.get(QrShare, share_key)
    if share is None or not hmac.compare_digest(share.start_token_hash, token_hash):
        fail("qr_denied", 403, "このQRでは閲覧を開始できません。", action="qr_start", target_type="qr_share")
    now = utcnow()
    _reject_closed_share(share, now)
    retry = _resume_attempt(share, attempt_hash, now)
    if retry is not None:
        session_row, raw = retry
        audit("qr_start_retry", "qr_view_session", session_row.id, "success")
        db.session.commit()
        return ActionResult(_start_body(session_row), qr_token=raw)
    if share.mode == "emergency":
        updated = db.session.execute(
            update(QrShare).where(
                QrShare.id == share.id,
                QrShare.consumed_at.is_(None),
                QrShare.stopped_at.is_(None),
                QrShare.mode == "emergency",
            ).values(consumed_at=now)
        ).rowcount
        if updated != 1:
            db.session.rollback()
            retry = _resume_attempt(share, attempt_hash, now)
            if retry is not None:
                session_row, raw = retry
                return ActionResult(_start_body(session_row), qr_token=raw)
            fail("qr_used", 409, "この緊急用QRはすでに使用されています。",
                 action="qr_start", target_type="qr_share", target_id=share.id)
    raw = secrets.token_bytes(32)
    absolute = now + timedelta(seconds=int(current_app.config["QR_VIEW_ABSOLUTE_SECONDS"]))
    idle_at = now + timedelta(seconds=int(current_app.config["QR_VIEW_IDLE_SECONDS"]))
    session_row = QrViewSession(
        qr_share_id=share.id,
        token_hash=hashlib.sha256(raw).digest(),
        attempt_hash=attempt_hash,
        issued_at=now,
        absolute_expires_at=absolute,
        idle_expires_at=idle_at if idle_at < absolute else absolute,
    )
    db.session.add(session_row)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        retry = _resume_attempt(share, attempt_hash, now)
        if retry is not None:
            session_row, raw = retry
            return ActionResult(_start_body(session_row), qr_token=raw)
        fail("qr_used", 409, "この緊急用QRはすでに使用されています。",
             action="qr_start", target_type="qr_share", target_id=share.id)
    audit("qr_started", "qr_view_session", session_row.id, "success")
    db.session.commit()
    return ActionResult(_start_body(session_row), qr_token=raw)


def _start_body(session_row: QrViewSession) -> dict:
    return {
        "session_id": session_row.id,
        "absolute_expires_at": iso(session_row.absolute_expires_at),
        "idle_expires_at": iso(session_row.idle_expires_at),
    }


def current_view() -> dict:
    session_row = _session_from_cookie()
    now = utcnow()
    if session_row.absolute_expires_at <= now or session_row.idle_expires_at <= now:
        fail("qr_session_expired", 401, "閲覧セッションの期限が切れています。")
    share = db.session.get(QrShare, session_row.qr_share_id)
    if share is None or share.stopped_at is not None:
        fail("qr_stopped", 403, "このQRは停止されています。")
    if share.mode == "timed" and (share.expires_at is None or share.expires_at <= now):
        fail("qr_expired", 403, "このQRの期限が切れています。")
    idle_at = now + timedelta(seconds=int(current_app.config["QR_VIEW_IDLE_SECONDS"]))
    session_row.idle_expires_at = idle_at if idle_at < session_row.absolute_expires_at else session_row.absolute_expires_at
    version = db.session.query(QrShareVersion).filter_by(share_id=share.id, version=share.current_version).one()
    db.session.commit()
    body = _view(share, version)
    body["session"] = _start_body(session_row)
    return body


def end_view() -> ActionResult:
    session_row = _session_from_cookie()
    if session_row.revoked_at is None:
        session_row.revoked_at = utcnow()
        audit("qr_ended", "qr_view_session", session_row.id, "success")
        db.session.commit()
    return ActionResult({"ok": True}, clear_qr=True)
