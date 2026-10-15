"""旧端末と新端末の鍵束移行。サーバーは鍵束を復号しない。"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from flask import current_app, request
from sqlalchemy import update

from app.extensions import db
from app.models import AuthSession, DeviceMigration, PasskeyCredential, User, utcnow
from app.services.auth_service import (
    _credential,
    _envelope,
    _label,
    _save_passkey,
    _start_challenge,
    _transports,
    _verify_registration,
)
from app.services.support import (
    audit,
    b64decode,
    b64encode,
    ciphertext_limit,
    expect_fields,
    fail,
    iso,
    load_login,
    require_reauth,
)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TERMINAL = {"finalized", "cancelled", "expired"}


def _fingerprint(public_key: bytes) -> str:
    digest = hashlib.sha256(public_key).hexdigest()[:16]
    return " ".join(digest[i:i + 4] for i in range(0, 16, 4))


def _code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))


def _normalize_code(value) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def _codes_match(stored: str | None, given) -> bool:
    if stored is None:
        return False
    left = _normalize_code(stored)
    right = _normalize_code(given)
    return len(left) == len(right) and hmac.compare_digest(left, right)


def _source():
    user, session_row, passkey = load_login()
    require_reauth(session_row)
    return user, session_row, passkey


def _owned(migration_id: str, user: User) -> DeviceMigration:
    row = db.session.get(DeviceMigration, migration_id)
    if row is None or row.user_id != user.id:
        fail("not_found", 404, "移行手続きが見つかりません。", actor_id=user.id)
    _ensure_current(row)
    return row


def _ensure_current(row: DeviceMigration) -> None:
    if row.status in TERMINAL:
        return
    if row.expires_at <= utcnow():
        _abort(row, "expired")
        db.session.commit()
        fail("migration_expired", 410, "移行の期限が切れました。古いパスキーはそのまま使えます。",
             action="migration_expired", target_type="migration", target_id=row.id, actor_id=row.user_id)


def _clear(row: DeviceMigration, status: str) -> None:
    row.status = status
    row.confirmation_code = None
    row.target_token_hash = None
    row.bundle_enc = None
    row.bundle_ciphertext = None
    if status == "finalized":
        row.finalized_at = utcnow()


def _revoke_target(row: DeviceMigration) -> None:
    if not row.target_passkey_id:
        return
    passkey = db.session.get(PasskeyCredential, row.target_passkey_id)
    if passkey is None or passkey.revoked_at is not None or passkey.user_id != row.user_id:
        return
    now = utcnow()
    passkey.revoked_at = now
    db.session.execute(
        update(AuthSession).where(
            AuthSession.passkey_id == passkey.id,
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=now)
    )


def _abort(row: DeviceMigration, status: str) -> None:
    # 取消と期限切れでは移行先だけを無効化し、移行元のパスキーは残す。
    _revoke_target(row)
    _clear(row, status)


def _view(row: DeviceMigration, *, include_code: bool) -> dict:
    body = {
        "id": row.id,
        "status": row.status,
        "expires_at": iso(row.expires_at),
        "fingerprint": None if row.target_public_key is None else _fingerprint(row.target_public_key),
        "target_public_key": None if row.target_public_key is None else b64encode(row.target_public_key),
        "source_approved": row.source_approved_at is not None,
    }
    if include_code:
        body["confirmation_code"] = row.confirmation_code
    return body


def start_migration() -> dict:
    from app.services.support import enforce_rate
    enforce_rate("migration")
    user, session_row, passkey = _source()
    now = utcnow()
    row = DeviceMigration(
        user_id=user.id,
        source_passkey_id=passkey.id,
        source_session_id=session_row.id,
        status="started",
        expires_at=now + timedelta(seconds=int(current_app.config["MIGRATION_TTL_SECONDS"])),
        created_at=now,
    )
    db.session.add(row)
    audit("migration_started", "migration", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"migration": _view(row, include_code=False)}


def _by_target_token() -> DeviceMigration:
    raw = request.headers.get("X-Transfer-Token")
    if not isinstance(raw, str):
        fail("migration_denied", 403, "移行先として確認できません。")
    token = b64decode(raw, exact=32)
    digest = hashlib.sha256(token).digest()
    row = db.session.query(DeviceMigration).filter_by(target_token_hash=digest).one_or_none()
    if row is None:
        fail("migration_denied", 403, "移行先として確認できません。")
    _ensure_current(row)
    if row.status in TERMINAL:
        fail("migration_denied", 403, "この移行は終了しています。")
    return row


def bind(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("migration")
    expect_fields(body, {"migration_id", "target_public_key"})
    row = db.session.get(DeviceMigration, body.get("migration_id"))
    if row is None:
        fail("not_found", 404, "移行手続きが見つかりません。")
    _ensure_current(row)
    public_key = b64decode(body.get("target_public_key"), exact=32)
    if row.status == "started" and row.target_public_key is None:
        row.target_public_key = public_key
        row.status = "bound"
        row.confirmation_code = _code()
        token = secrets.token_bytes(32)
        row.target_token_hash = hashlib.sha256(token).digest()
        audit("migration_bound", "migration", row.id, "success", actor_id=row.user_id)
        db.session.commit()
        return {"migration": _view(row, include_code=True), "transfer_token": b64encode(token)}
    if row.status == "bound" and row.target_public_key is not None and hmac.compare_digest(row.target_public_key, public_key):
        token = secrets.token_bytes(32)
        row.target_token_hash = hashlib.sha256(token).digest()
        db.session.commit()
        return {"migration": _view(row, include_code=True), "transfer_token": b64encode(token)}
    fail("migration_key_mismatch", 409, "移行先の公開鍵は変更できません。手続きをやり直してください。",
         action="migration_bind", target_type="migration", target_id=row.id, actor_id=row.user_id)


def source_status(migration_id: str) -> dict:
    user, _session, passkey = _source()
    row = _owned(migration_id, user)
    if row.source_passkey_id != passkey.id:
        fail("not_found", 404, "移行手続きが見つかりません。", actor_id=user.id)
    return {"migration": _view(row, include_code=True)}


def target_status() -> dict:
    row = _by_target_token()
    return {"migration": _view(row, include_code=True)}


def approve(migration_id: str, body: dict) -> dict:
    expect_fields(body, {"confirmation_code", "fingerprint"})
    user, _session, passkey = _source()
    row = _owned(migration_id, user)
    if row.source_passkey_id != passkey.id or row.status != "bound" or row.target_public_key is None:
        fail("migration_state", 409, "いま承認できる状態ではありません。", actor_id=user.id)
    expected = _fingerprint(row.target_public_key)
    code_ok = _codes_match(row.confirmation_code, body.get("confirmation_code"))
    finger = body.get("fingerprint")
    finger_ok = (
        isinstance(finger, str) and len(finger) == len(expected) and hmac.compare_digest(finger, expected)
    )
    if not code_ok or not finger_ok:
        row.approve_failures += 1
        if row.approve_failures >= 5:
            _abort(row, "cancelled")
            audit("migration_cancelled", "migration", row.id, "failure", "mismatch", actor_id=user.id)
            db.session.commit()
            fail("migration_cancelled", 409, "照合が合わなかったため、移行を取り消しました。古いパスキーはそのまま使えます。",
                 actor_id=user.id)
        db.session.commit()
        fail("migration_mismatch", 403, "照合コードまたは指紋が一致しません。",
             action="migration_approve", target_type="migration", target_id=row.id, actor_id=user.id)
    row.status = "source_approved"
    row.source_approved_at = utcnow()
    audit("migration_approved", "migration", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"migration": _view(row, include_code=True)}


def upload_bundle(migration_id: str, body: dict) -> dict:
    expect_fields(body, {"hpke_enc", "ciphertext"})
    user, _session, passkey = _source()
    row = _owned(migration_id, user)
    if row.source_passkey_id != passkey.id or row.status != "source_approved":
        fail("migration_state", 409, "鍵束を送れる状態ではありません。", actor_id=user.id)
    row.bundle_enc = b64decode(body.get("hpke_enc"), exact=32)
    row.bundle_ciphertext = b64decode(body.get("ciphertext"), minimum=16, maximum=ciphertext_limit())
    row.status = "bundled"
    audit("migration_bundled", "migration", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"ok": True, "status": row.status}


def download_bundle() -> dict:
    row = _by_target_token()
    if row.status not in {"bundled", "target_ready"} or row.bundle_enc is None or row.bundle_ciphertext is None:
        fail("migration_state", 409, "鍵束はまだ届いていません。")
    return {
        "hpke_enc": b64encode(row.bundle_enc),
        "ciphertext": b64encode(row.bundle_ciphertext),
        "migration_id": row.id,
        "user_id": row.user_id,
    }


def begin_target_passkey() -> dict:
    row = _by_target_token()
    if row.status != "bundled":
        fail("migration_state", 409, "パスキーを登録できる状態ではありません。")
    user = db.session.get(User, row.user_id)
    if user is None:
        fail("migration_state", 409, "移行元のアカウントを確認できません。")
    existing = db.session.query(PasskeyCredential).filter_by(user_id=user.id, revoked_at=None).all()
    from app.services.auth_service import _current_data_key
    data_key = _current_data_key(user.id)
    result = _start_challenge(
        "add_passkey", user.id,
        {
            "data_key_id": data_key.id,
            "display_name": user.display_name or "利用者",
            "user_handle": b64encode(user.webauthn_user_handle),
            "migration_id": row.id,
        },
        secrets.token_bytes(32),
        True,
        [item.credential_id for item in existing],
    )
    result.body["migration_id"] = row.id
    return result


def finish_target_passkey(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("auth_verify")
    expect_fields(body, {"credential", "envelope", "label"})
    row = _by_target_token()
    if row.status != "bundled":
        fail("migration_state", 409, "パスキーを登録できる状態ではありません。")
    user = db.session.get(User, row.user_id)
    from app.services.support import consume_challenge
    challenge = consume_challenge("add_passkey", user_id=user.id)
    if challenge.request_options.get("migration_id") != row.id:
        fail("migration_state", 409, "移行の手続きとパスキー登録が一致しません。")
    credential = _credential(body)
    verified = _verify_registration(credential, challenge.challenge)
    if db.session.query(PasskeyCredential).filter_by(credential_id=verified.credential_id).first():
        fail("credential_exists", 409, "このパスキーはすでに登録されています。", actor_id=user.id)
    wrapped = _envelope(body, challenge.request_options["prf_input"])
    passkey = _save_passkey(user, verified, _label(body), _transports(credential))
    from app.crypto_contract import CRYPTO_VERSION
    from app.models import UserKeyEnvelope
    db.session.add(UserKeyEnvelope(
        user_id=user.id,
        data_key_id=challenge.request_options["data_key_id"],
        passkey_id=passkey.id,
        kdf_version=1,
        crypto_version=CRYPTO_VERSION,
        **wrapped,
    ))
    row.target_passkey_id = passkey.id
    row.status = "target_ready"
    audit("migration_passkey", "passkey", passkey.id, "success", actor_id=user.id)
    db.session.commit()
    return {"passkey_id": passkey.id, "status": row.status}


def finalize(migration_id: str, body: dict) -> dict:
    expect_fields(body, {"confirmed"})
    if body.get("confirmed") is not True:
        fail("confirmation_required", 400, "復号できたことを確認してから完了してください。")
    user, session_row, passkey = load_login()
    row = db.session.get(DeviceMigration, migration_id)
    if row is None or row.user_id != user.id:
        fail("not_found", 404, "移行手続きが見つかりません。", actor_id=user.id)
    _ensure_current(row)
    if (row.status != "target_ready" or row.target_passkey_id != passkey.id
            or session_row.passkey_id != passkey.id or row.source_approved_at is None):
        fail("migration_state", 409, "移行を完了できる状態ではありません。古いパスキーは無効化していません。",
             action="migration_finalize", target_type="migration", target_id=row.id, actor_id=user.id)
    source = db.session.get(PasskeyCredential, row.source_passkey_id)
    if source is None or source.revoked_at is not None or source.user_id != user.id:
        fail("migration_state", 409, "移行元のパスキーを確認できません。", actor_id=user.id)
    now = utcnow()
    source.revoked_at = now
    db.session.execute(
        update(AuthSession).where(
            AuthSession.passkey_id == source.id,
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=now)
    )
    _clear(row, "finalized")
    audit("migration_finalized", "passkey", source.id, "success", actor_id=user.id)
    db.session.commit()
    return {"ok": True, "status": "finalized", "revoked_passkey_id": source.id}


def cancel(migration_id: str) -> dict:
    user = None
    row = None
    token_error = None
    try:
        user, _session, passkey = load_login()
        row = db.session.get(DeviceMigration, migration_id)
        if row is None or row.user_id != user.id or row.source_passkey_id != passkey.id:
            row = None
    except Exception:
        row = None
    if row is None:
        try:
            row = _by_target_token()
            if row.id != migration_id:
                row = None
        except Exception as exc:
            token_error = exc
    if row is None:
        if token_error is not None:
            raise token_error
        fail("not_found", 404, "移行手続きが見つかりません。", actor_id=None if user is None else user.id)
    if row.status in TERMINAL:
        return {"ok": True, "status": row.status}
    _abort(row, "cancelled")
    audit("migration_cancelled", "migration", row.id, "success", actor_id=row.user_id)
    db.session.commit()
    return {"ok": True, "status": "cancelled"}
