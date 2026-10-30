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
OPEN = ("started", "bound", "source_approved", "bundled", "target_ready")


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
        if not _abort(row, "expired"):
            # 同時に完了・取消された。その結果をそのまま使う。
            db.session.commit()
            return
        db.session.commit()
        fail("migration_expired", 410, "移行の期限が切れました。古いパスキーはそのまま使えます。",
             action="migration_expired", target_type="migration", target_id=row.id, actor_id=row.user_id)


def _move(row: DeviceMigration, allowed: tuple[str, ...], status: str) -> bool:
    """移行を終える状態変化を、条件付きUPDATE1回で行う。条件に合ったときだけ変わり、行を読み直す。

    読んでから書くと、取消とパスキー登録・完了が同時に起きたとき、互いの結果を上書きするため。
    PostgreSQLは行ロックと条件の再評価、SQLiteは書き込みロックで直列になる。
    """
    values = {
        "status": status,
        "confirmation_code": None,
        "target_token_hash": None,
        "bundle_enc": None,
        "bundle_ciphertext": None,
    }
    if status == "finalized":
        values["finalized_at"] = utcnow()
    moved = db.session.execute(
        update(DeviceMigration).where(DeviceMigration.id == row.id, DeviceMigration.status.in_(allowed))
        .values(**values).execution_options(synchronize_session=False)
    ).rowcount == 1
    db.session.refresh(row)
    return moved


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


def _advance(row: DeviceMigration, *conditions, **values) -> bool:
    """手続きを進める状態変化も、今の状態を条件にしたUPDATE1回で行う。取り消し済みの移行を生き返らせない。"""
    moved = db.session.execute(
        update(DeviceMigration).where(DeviceMigration.id == row.id, *conditions)
        .values(**values).execution_options(synchronize_session=False)
    ).rowcount == 1
    if moved:
        db.session.refresh(row)
    return moved


def _abort(row: DeviceMigration, status: str) -> bool:
    """取消と期限切れでは移行先だけを無効化し、移行元のパスキーは残す。終わっていた場合はFalse。

    状態を先に変えてから、読み直した行の移行先パスキーを無効化する。
    取消の直前に登録が確定していても、そのパスキーを取りこぼさない。
    """
    if not _move(row, OPEN, status):
        return False
    _revoke_target(row)
    return True


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
        token = secrets.token_bytes(32)
        if _advance(row, DeviceMigration.status == "started", DeviceMigration.target_public_key.is_(None),
                    target_public_key=public_key, status="bound", confirmation_code=_code(),
                    target_token_hash=hashlib.sha256(token).digest()):
            audit("migration_bound", "migration", row.id, "success", actor_id=row.user_id)
            db.session.commit()
            return {"migration": _view(row, include_code=True), "transfer_token": b64encode(token)}
        db.session.rollback()
        db.session.refresh(row)
    if row.status == "bound" and row.target_public_key is not None and hmac.compare_digest(row.target_public_key, public_key):
        token = secrets.token_bytes(32)
        if _advance(row, DeviceMigration.status == "bound", DeviceMigration.target_public_key == public_key,
                    target_token_hash=hashlib.sha256(token).digest()):
            db.session.commit()
            return {"migration": _view(row, include_code=True), "transfer_token": b64encode(token)}
        db.session.rollback()
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
        # 誤りの回数もDB上で加算する。同時に送っても5回を超えて試せない。
        counted = _advance(row, DeviceMigration.status == "bound", DeviceMigration.approve_failures < 5,
                           approve_failures=DeviceMigration.approve_failures + 1)
        if not counted:
            db.session.rollback()
            fail("migration_state", 409, "いま承認できる状態ではありません。", actor_id=user.id)
        if row.approve_failures >= 5:
            _abort(row, "cancelled")
            audit("migration_cancelled", "migration", row.id, "failure", "mismatch", actor_id=user.id)
            db.session.commit()
            fail("migration_cancelled", 409, "照合が合わなかったため、移行を取り消しました。古いパスキーはそのまま使えます。",
                 actor_id=user.id)
        db.session.commit()
        fail("migration_mismatch", 403, "照合コードまたは指紋が一致しません。",
             action="migration_approve", target_type="migration", target_id=row.id, actor_id=user.id)
    if not _advance(row, DeviceMigration.status == "bound", DeviceMigration.source_passkey_id == passkey.id,
                    status="source_approved", source_approved_at=utcnow()):
        fail("migration_state", 409, "いま承認できる状態ではありません。", actor_id=user.id)
    audit("migration_approved", "migration", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"migration": _view(row, include_code=True)}


def upload_bundle(migration_id: str, body: dict) -> dict:
    expect_fields(body, {"hpke_enc", "ciphertext"})
    user, _session, passkey = _source()
    row = _owned(migration_id, user)
    if row.source_passkey_id != passkey.id or row.status != "source_approved":
        fail("migration_state", 409, "鍵束を送れる状態ではありません。", actor_id=user.id)
    enc = b64decode(body.get("hpke_enc"), exact=32)
    ciphertext = b64decode(body.get("ciphertext"), minimum=16, maximum=ciphertext_limit())
    if not _advance(row, DeviceMigration.status == "source_approved", DeviceMigration.source_passkey_id == passkey.id,
                    bundle_enc=enc, bundle_ciphertext=ciphertext, status="bundled"):
        fail("migration_state", 409, "鍵束を送れる状態ではありません。", actor_id=user.id)
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
    token_hash = row.target_token_hash
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
    db.session.flush()
    # 検証の間に取り消された場合は、パスキーと鍵の封筒ごとロールバックし、登録しない。
    moved = db.session.execute(
        update(DeviceMigration).where(
            DeviceMigration.id == row.id,
            DeviceMigration.status == "bundled",
            DeviceMigration.target_token_hash == token_hash,
            DeviceMigration.expires_at > utcnow(),
        ).values(status="target_ready", target_passkey_id=passkey.id)
        .execution_options(synchronize_session=False)
    ).rowcount == 1
    if not moved:
        fail("migration_state", 409, "移行が取り消されたか状態が変わったため、パスキーを登録しませんでした。",
             action="migration_passkey", target_type="migration", target_id=row.id, actor_id=user.id)
    audit("migration_passkey", "passkey", passkey.id, "success", actor_id=user.id)
    db.session.commit()
    return {"passkey_id": passkey.id, "status": "target_ready"}


def _target_session(migration_id: str):
    user, session_row, passkey = load_login()
    require_reauth(session_row)
    row = db.session.get(DeviceMigration, migration_id)
    if row is None or row.user_id != user.id:
        fail("not_found", 404, "移行手続きが見つかりません。", actor_id=user.id)
    _ensure_current(row)
    if (row.status != "target_ready" or row.target_passkey_id != passkey.id
            or session_row.passkey_id != passkey.id or row.source_approved_at is None
            or row.source_passkey_id == passkey.id):
        fail("migration_state", 409, "移行を完了できる状態ではありません。古いパスキーは無効化していません。",
             action="migration_finalize", target_type="migration", target_id=row.id, actor_id=user.id)
    return user, session_row, passkey, row


def acknowledge_decrypt(migration_id: str, body: dict) -> dict:
    expect_fields(body, set())
    user, _session_row, _passkey, row = _target_session(migration_id)
    if row.decrypt_confirmed_at is None:
        row.decrypt_confirmed_at = utcnow()
        audit("migration_decrypt_confirmed", "migration", row.id, "success", actor_id=user.id)
        db.session.commit()
    return {"ok": True, "status": row.status, "decrypt_confirmed": True}


def finalize(migration_id: str, body: dict) -> dict:
    expect_fields(body, {"confirmed"})
    if body.get("confirmed") is not True:
        fail("confirmation_required", 400, "復号できたことを確認してから完了してください。")
    user, _session_row, passkey, row = _target_session(migration_id)
    if row.decrypt_confirmed_at is None:
        fail("decrypt_unconfirmed", 409,
             "新しいパスキーで保管用鍵と既存データを開けたことを確認してから完了してください。古いパスキーは無効化していません。",
             action="migration_finalize", target_type="migration", target_id=row.id, actor_id=user.id)
    source = db.session.get(PasskeyCredential, row.source_passkey_id)
    if source is None or source.revoked_at is not None or source.user_id != user.id:
        fail("migration_state", 409, "移行元のパスキーを確認できません。", actor_id=user.id)
    # 取消と同時でも、どちらか一方だけが成立する。完了できたときだけ移行元を無効化する。
    if not _move(row, ("target_ready",), "finalized"):
        fail("migration_state", 409, "移行が取り消されたか状態が変わったため、完了できませんでした。古いパスキーは無効化していません。",
             action="migration_finalize", target_type="migration", target_id=row.id, actor_id=user.id)
    now = utcnow()
    source.revoked_at = now
    db.session.execute(
        update(AuthSession).where(
            AuthSession.passkey_id == source.id,
            AuthSession.revoked_at.is_(None),
        ).values(revoked_at=now)
    )
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
    if not _abort(row, "cancelled"):
        # 同時に完了・期限切れになった。終わった状態をそのまま返す。
        db.session.commit()
        return {"ok": True, "status": row.status}
    audit("migration_cancelled", "migration", row.id, "success", actor_id=row.user_id)
    db.session.commit()
    return {"ok": True, "status": "cancelled"}
