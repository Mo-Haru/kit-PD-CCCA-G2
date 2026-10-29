"""パスキー認証、鍵エンベロープ、受信用公開鍵。復号はしない。"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import update
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json_dict
from webauthn.helpers.structs import (
    AttestationConveyancePreference,
    AuthenticatorSelectionCriteria,
    CredentialDeviceType,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.crypto_contract import CRYPTO_VERSION
from app.extensions import db
from app.models import (
    AuthSession,
    FamilyConnection,
    FamilyShare,
    PasskeyCredential,
    User,
    UserDataKey,
    UserEncryptionKey,
    UserKeyBundle,
    UserKeyEnvelope,
    WebAuthnChallenge,
    utcnow,
)
from app.services.support import (
    ActionResult,
    ApiError,
    audit,
    b64decode,
    b64encode,
    ciphertext_limit,
    clean_display_name,
    commit_or_conflict,
    consume_challenge,
    enforce_rate,
    expect_fields,
    fail,
    iso,
    load_login,
    mark_reauth,
    require_reauth,
    touch_flow,
)

ALLOWED_TRANSPORTS = {"usb", "nfc", "ble", "smart-card", "internal", "cable", "hybrid"}


def _rp() -> tuple[str, str, str]:
    return (
        current_app.config["WEBAUTHN_RP_ID"],
        current_app.config["WEBAUTHN_RP_NAME"],
        current_app.config["WEBAUTHN_ORIGIN"],
    )


def _public_key(options, prf_input: bytes | None = None) -> dict:
    data = options_to_json_dict(options)
    for param in data.get("pubKeyCredParams", []):
        param["alg"] = int(param["alg"])
    if not data.get("allowCredentials"):
        data.pop("allowCredentials", None)
    if not data.get("excludeCredentials"):
        data.pop("excludeCredentials", None)
    if prf_input is not None:
        data["extensions"] = {"prf": {"eval": {"first": b64encode(prf_input)}}}
    return data


def _selection() -> AuthenticatorSelectionCriteria:
    return AuthenticatorSelectionCriteria(
        resident_key=ResidentKeyRequirement.REQUIRED,
        user_verification=UserVerificationRequirement.REQUIRED,
    )


def _new_challenge(purpose: str, flow_hash: bytes, options: dict, user_id: str | None = None) -> None:
    now = utcnow()
    db.session.add(WebAuthnChallenge(
        challenge=b64decode(options["challenge_b64"], exact=32),
        purpose=purpose,
        user_id=user_id,
        flow_binding_hash=flow_hash,
        request_options={key: value for key, value in options.items() if key != "challenge_b64"},
        created_at=now,
        expires_at=now + timedelta(seconds=int(current_app.config["CHALLENGE_TTL_SECONDS"])),
    ))
    db.session.commit()


def _start_challenge(purpose: str, user_id: str | None, extra: dict, prf_input: bytes,
                     registration: bool, allow_ids: list[bytes] | None = None) -> ActionResult:
    enforce_rate("auth_options")
    rp_id, rp_name, _origin = _rp()
    challenge = secrets.token_bytes(32)
    flow_hash, flow_cookie = touch_flow()
    descriptors = [PublicKeyCredentialDescriptor(id=item) for item in (allow_ids or [])] or None
    if registration:
        handle = b64decode(extra["user_handle"], exact=32)
        options = generate_registration_options(
            rp_id=rp_id,
            rp_name=rp_name,
            user_name=extra["display_name"],
            user_display_name=extra["display_name"],
            user_id=handle,
            challenge=challenge,
            timeout=60000,
            attestation=AttestationConveyancePreference.NONE,
            authenticator_selection=_selection(),
            exclude_credentials=descriptors,
        )
    else:
        options = generate_authentication_options(
            rp_id=rp_id,
            challenge=challenge,
            timeout=60000,
            allow_credentials=descriptors,
            user_verification=UserVerificationRequirement.REQUIRED,
        )
    public_key = _public_key(options, prf_input)
    stored = dict(extra)
    stored["challenge_b64"] = b64encode(challenge)
    stored["prf_input"] = b64encode(prf_input)
    _new_challenge(purpose, flow_hash, stored, user_id)
    body = {"public_key": public_key, "prf_input": b64encode(prf_input)}
    body.update({key: value for key, value in extra.items() if key != "challenge_b64"})
    return ActionResult(body, flow_cookie=flow_cookie)


def begin_registration(body: dict) -> ActionResult:
    expect_fields(body, {"display_name"})
    name = clean_display_name(body.get("display_name"))
    handle = secrets.token_bytes(32)
    prf_input = secrets.token_bytes(32)
    data_key_id = str(uuid.uuid4())
    return _start_challenge(
        "register", None,
        {"display_name": name, "user_handle": b64encode(handle), "data_key_id": data_key_id},
        prf_input, True,
    )


def _credential(body: dict) -> dict:
    credential = body.get("credential")
    from app.services.support import assert_credential_safe
    assert_credential_safe(credential)
    return credential


def _verify_registration(credential, challenge: bytes):
    _rp_id, _name, origin = _rp()
    try:
        return verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=_rp_id,
            expected_origin=origin,
            require_user_verification=True,
        )
    except Exception:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。",
             action="auth_register", target_type="passkey")


def _verify_assertion(credential, challenge: bytes, public_key: bytes, sign_count: int):
    rp_id, _name, origin = _rp()
    try:
        return verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origin,
            credential_public_key=public_key,
            credential_current_sign_count=sign_count,
            require_user_verification=True,
        )
    except Exception:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。",
             action="auth_login", target_type="passkey")


def _envelope(body: dict, expected_prf: str) -> dict:
    envelope = body.get("envelope")
    if not isinstance(envelope, dict):
        raise fail("invalid_envelope", 400, "鍵の保護情報が必要です。")
    expect_fields(envelope, {"prf_input", "kdf_salt", "wrapped_key", "nonce", "kdf_version", "crypto_version"})
    if envelope.get("kdf_version") != 1 or envelope.get("crypto_version") != CRYPTO_VERSION:
        fail("unsupported_crypto", 400, "未対応の暗号方式です。")
    if envelope.get("prf_input") != expected_prf:
        fail("invalid_envelope", 400, "鍵の保護情報が手続きと一致しません。")
    return {
        "prf_input": b64decode(envelope["prf_input"], exact=32),
        "kdf_salt": b64decode(envelope["kdf_salt"], exact=32),
        "wrapped_key": b64decode(envelope["wrapped_key"], exact=48),
        "nonce": b64decode(envelope["nonce"], exact=12),
    }


def _bundle(body: dict) -> tuple[bytes, bytes]:
    bundle = body.get("key_bundle")
    if not isinstance(bundle, dict):
        fail("invalid_bundle", 400, "暗号化された鍵束が必要です。")
    expect_fields(bundle, {"ciphertext", "nonce", "crypto_version"})
    if bundle.get("crypto_version") != CRYPTO_VERSION:
        fail("unsupported_crypto", 400, "未対応の暗号方式です。")
    return (
        b64decode(bundle["ciphertext"], minimum=16, maximum=ciphertext_limit()),
        b64decode(bundle["nonce"], exact=12),
    )


def _label(body: dict) -> str | None:
    if "label" not in body or body["label"] is None:
        return None
    return clean_display_name(body["label"])


def _transports(credential: dict) -> list[str]:
    raw = credential.get("response", {}).get("transports", [])
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, str) and item in ALLOWED_TRANSPORTS][:8]


def _save_passkey(user: User, verified, label: str | None, transports: list[str], *, proven: bool = True) -> PasskeyCredential:
    backed = bool(verified.credential_backed_up)
    device_type = getattr(verified, "credential_device_type", CredentialDeviceType.SINGLE_DEVICE)
    eligible = backed or device_type == CredentialDeviceType.MULTI_DEVICE
    aaguid = verified.aaguid if isinstance(verified.aaguid, str) and len(verified.aaguid) <= 36 else None
    row = PasskeyCredential(
        user_id=user.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=int(verified.sign_count),
        transports=transports,
        backup_eligible=eligible,
        backup_state=backed,
        uv_initialized=bool(verified.user_verified),
        prf_enabled=True,
        aaguid=aaguid,
        label=label,
        last_used_at=utcnow(),
        proven_at=utcnow() if proven else None,
    )
    db.session.add(row)
    db.session.flush()
    return row


def _issue_session(user: User, passkey: PasskeyCredential, now):
    token = secrets.token_bytes(32)
    row = AuthSession(
        user_id=user.id,
        passkey_id=passkey.id,
        token_hash=hashlib.sha256(token).digest(),
        authenticated_at=now,
        expires_at=now + timedelta(seconds=int(current_app.config["SESSION_TTL_SECONDS"])),
    )
    db.session.add(row)
    db.session.flush()
    return row, token


def finish_registration(body: dict) -> ActionResult:
    enforce_rate("auth_verify")
    expect_fields(body, {"credential", "envelope", "encryption_public_key", "key_bundle", "label"})
    challenge = consume_challenge("register")
    credential = _credential(body)
    verified = _verify_registration(credential, challenge.challenge)
    if db.session.query(PasskeyCredential).filter_by(credential_id=verified.credential_id).first():
        fail("credential_exists", 409, "このパスキーはすでに登録されています。")
    # 検証に成功した登録だけを数える（偽の検証要求でサイト全体の枠を使い切られないように）。
    # 行を追加する前に呼ぶ。enforce_rateは数えた回数をその場で確定させる。
    enforce_rate("register", message="この接続元からの新規登録が多すぎます。時間をおいてやり直してください。")
    enforce_rate("register_global", ident="all",
                 message="現在、新規登録を一時的に制限しています。時間をおいてやり直してください。")
    stored = challenge.request_options
    wrapped = _envelope(body, stored["prf_input"])
    bundle_ct, bundle_nonce = _bundle(body)
    public_key = b64decode(body.get("encryption_public_key"), exact=32)
    handle = b64decode(stored["user_handle"], exact=32)
    now = utcnow()
    user = User(webauthn_user_handle=handle, display_name=stored["display_name"], status="active")
    db.session.add(user)
    db.session.flush()
    data_key = UserDataKey(id=stored["data_key_id"], user_id=user.id, key_version=1)
    db.session.add(data_key)
    passkey = _save_passkey(user, verified, _label(body), _transports(credential))
    db.session.add(UserKeyEnvelope(
        user_id=user.id, data_key_id=data_key.id, passkey_id=passkey.id,
        kdf_version=1, crypto_version=CRYPTO_VERSION, **wrapped,
    ))
    enc = UserEncryptionKey(user_id=user.id, purpose="receive", public_key=public_key, status="active")
    db.session.add(enc)
    db.session.add(UserKeyBundle(
        user_id=user.id, data_key_id=data_key.id, revision=1,
        ciphertext=bundle_ct, nonce=bundle_nonce, crypto_version=CRYPTO_VERSION,
    ))
    session_row, token = _issue_session(user, passkey, now)
    mark_reauth(session_row, now)
    audit("auth_register", "user", user.id, "success", actor_id=user.id)
    commit_or_conflict("アカウントを作成できませんでした。最初からやり直してください。")
    return ActionResult({
        "user_id": user.id,
        "display_name": user.display_name,
        "passkey_id": passkey.id,
        "data_key_id": data_key.id,
        "encryption_key_id": enc.id,
    }, session_token=token)


def begin_login(body: dict) -> ActionResult:
    expect_fields(body, set())
    prf_input = secrets.token_bytes(32)
    # ログイン時点ではどのパスキーか不明なため、PRFのsaltは解除手続きで渡す。
    result = _start_challenge("login", None, {}, prf_input, False)
    result.body.pop("prf_input", None)
    result.body["public_key"].pop("extensions", None)
    return result


def _passkey_by_credential(credential: dict) -> PasskeyCredential:
    raw_id = credential.get("rawId")
    if not isinstance(raw_id, str):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。", action="auth_login", target_type="passkey")
    found = db.session.query(PasskeyCredential).filter_by(credential_id=b64decode(raw_id)).one_or_none()
    if found is None or found.revoked_at is not None:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。", action="auth_login", target_type="passkey")
    return found


def _same_bytes(left: bytes, right: bytes) -> bool:
    import hmac
    return isinstance(left, bytes) and isinstance(right, bytes) and hmac.compare_digest(left, right)


def _user_handle_matches(credential: dict, user: User) -> None:
    handle = credential.get("response", {}).get("userHandle")
    if not isinstance(handle, str):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。",
             action="auth_login", target_type="user", target_id=user.id)
    try:
        decoded = b64decode(handle, exact=32)
    except Exception:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。",
             action="auth_login", target_type="user", target_id=user.id)
    if not _same_bytes(decoded, user.webauthn_user_handle):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。",
             action="auth_login", target_type="user", target_id=user.id)


def _envelope_for(passkey: PasskeyCredential) -> UserKeyEnvelope | None:
    return (
        db.session.query(UserKeyEnvelope)
        .filter_by(passkey_id=passkey.id, user_id=passkey.user_id)
        .order_by(UserKeyEnvelope.created_at.desc())
        .first()
    )


def envelope_view(row: UserKeyEnvelope) -> dict:
    return {
        "data_key_id": row.data_key_id,
        "passkey_id": row.passkey_id,
        "prf_input": b64encode(row.prf_input),
        "kdf_salt": b64encode(row.kdf_salt),
        "wrapped_key": b64encode(row.wrapped_key),
        "nonce": b64encode(row.nonce),
        "kdf_version": row.kdf_version,
        "crypto_version": row.crypto_version,
    }


def bundle_view(row: UserKeyBundle) -> dict:
    return {
        "data_key_id": row.data_key_id,
        "revision": row.revision,
        "ciphertext": b64encode(row.ciphertext),
        "nonce": b64encode(row.nonce),
        "crypto_version": row.crypto_version,
    }


def latest_bundle(user_id: str, data_key_id: str) -> UserKeyBundle | None:
    return (
        db.session.query(UserKeyBundle)
        .filter_by(user_id=user_id, data_key_id=data_key_id)
        .order_by(UserKeyBundle.revision.desc())
        .first()
    )


def finish_login(body: dict) -> ActionResult:
    enforce_rate("auth_verify")
    expect_fields(body, {"credential"})
    challenge = consume_challenge("login")
    credential = _credential(body)
    passkey = _passkey_by_credential(credential)
    user = db.session.get(User, passkey.user_id)
    if user is None or user.status != "active":
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。", action="auth_login", target_type="passkey")
    _user_handle_matches(credential, user)
    verified = _verify_assertion(credential, challenge.challenge, passkey.public_key, passkey.sign_count)
    if not _same_bytes(verified.credential_id, passkey.credential_id):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。")
    if passkey.proven_at is None:
        passkey.sign_count = int(verified.new_sign_count)
        audit("auth_login", "passkey", passkey.id, "failure", "passkey_unproven", actor_id=user.id)
        db.session.commit()
        raise ApiError(
            "passkey_unproven", 403,
            "このパスキーは確認が済んでいません。確認済みのパスキーでログインしてください。",
        )
    # ライブラリが同期パスキーのsign count 0を受理した場合はその値を保存する。ここで0を拒否しない。
    passkey.sign_count = int(verified.new_sign_count)
    passkey.last_used_at = utcnow()
    session_row, token = _issue_session(user, passkey, utcnow())
    audit("auth_login", "user", user.id, "success", actor_id=user.id)
    db.session.commit()
    wrapped = _envelope_for(passkey)
    bundle = latest_bundle(user.id, wrapped.data_key_id) if wrapped else None
    return ActionResult({
        "user_id": user.id,
        "display_name": user.display_name,
        "passkey_id": passkey.id,
        "envelope": None if wrapped is None else envelope_view(wrapped),
        "key_bundle": None if bundle is None else bundle_view(bundle),
        "unlock_required": True,
    }, session_token=token)


def logout() -> ActionResult:
    user, session_row, _passkey = load_login()
    session_row.revoked_at = utcnow()
    audit("auth_logout", "auth_session", session_row.id, "success", actor_id=user.id)
    db.session.commit()
    return ActionResult({"ok": True}, clear_session=True)


def session_state() -> dict:
    try:
        user, session_row, passkey = load_login()
    except Exception as exc:
        from app.services.support import ApiError
        if isinstance(exc, ApiError) and exc.code == "unauthenticated":
            return {"authenticated": False}
        raise
    reauth = require_reauth_state(session_row)
    return {
        "authenticated": True,
        "user_id": user.id,
        "display_name": user.display_name,
        "passkey_id": passkey.id,
        "reauthenticated": reauth,
    }


def require_reauth_state(session_row: AuthSession) -> bool:
    try:
        require_reauth(session_row)
    except Exception:
        from app.services.support import ApiError
        return False
    return True


def begin_unlock() -> ActionResult:
    user, session_row, passkey = load_login()
    wrapped = _envelope_for(passkey)
    if wrapped is None:
        fail("key_unavailable", 409, "このパスキーで解除できる鍵が見つかりません。",
             action="auth_unlock", target_type="passkey", target_id=passkey.id, actor_id=user.id)
    prf_input = wrapped.prf_input
    result = _start_challenge(
        "reauthenticate", user.id, {"passkey_id": passkey.id}, prf_input, False, [passkey.credential_id],
    )
    result.body["envelope"] = envelope_view(wrapped)
    bundle = latest_bundle(user.id, wrapped.data_key_id)
    result.body["key_bundle"] = None if bundle is None else bundle_view(bundle)
    return result


def finish_unlock(body: dict) -> ActionResult:
    enforce_rate("auth_verify")
    expect_fields(body, {"credential"})
    user, session_row, passkey = load_login()
    challenge = consume_challenge("reauthenticate", user_id=user.id)
    if challenge.request_options.get("passkey_id") != passkey.id:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。")
    credential = _credential(body)
    _user_handle_matches(credential, user)
    verified = _verify_assertion(credential, challenge.challenge, passkey.public_key, passkey.sign_count)
    if not _same_bytes(verified.credential_id, passkey.credential_id):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。")
    passkey.sign_count = int(verified.new_sign_count)
    passkey.last_used_at = utcnow()
    mark_reauth(session_row)
    audit("auth_unlock", "passkey", passkey.id, "success", actor_id=user.id)
    db.session.commit()
    return ActionResult({"ok": True, "passkey_id": passkey.id})


def list_passkeys() -> dict:
    user, _session, _passkey = load_login()
    rows = (
        db.session.query(PasskeyCredential)
        .filter_by(user_id=user.id)
        .order_by(PasskeyCredential.created_at.asc())
        .all()
    )
    return {"passkeys": [{
        "id": row.id,
        "label": row.label,
        "created_at": iso(row.created_at),
        "last_used_at": iso(row.last_used_at),
        "revoked_at": iso(row.revoked_at),
        "proven_at": iso(row.proven_at),
        "backup_eligible": row.backup_eligible,
        "backup_state": row.backup_state,
        "prf_enabled": row.prf_enabled,
        "aaguid": row.aaguid,
    } for row in rows]}


def own_envelopes() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(UserKeyEnvelope).filter_by(user_id=user.id).all()
    bundles = db.session.query(UserKeyBundle).filter_by(user_id=user.id).all()
    return {
        "envelopes": [envelope_view(row) for row in rows],
        "key_bundles": [bundle_view(row) for row in bundles],
    }


def _current_data_key(user_id: str) -> UserDataKey:
    row = (
        db.session.query(UserDataKey)
        .filter_by(user_id=user_id, retired_at=None)
        .order_by(UserDataKey.key_version.desc())
        .first()
    )
    if row is None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。")
    return row


def _usable_passkeys(user_id: str):
    """確認済みで無効化されていないパスキー。確認待ちは件数に入れない。"""
    return db.session.query(PasskeyCredential).filter(
        PasskeyCredential.user_id == user_id,
        PasskeyCredential.revoked_at.is_(None),
        PasskeyCredential.proven_at.is_not(None),
    )


def begin_add_passkey() -> ActionResult:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    data_key = _current_data_key(user.id)
    existing = db.session.query(PasskeyCredential).filter_by(user_id=user.id, revoked_at=None).all()
    prf_input = secrets.token_bytes(32)
    return _start_challenge(
        "add_passkey", user.id,
        {"data_key_id": data_key.id, "display_name": user.display_name or "利用者",
         "user_handle": b64encode(user.webauthn_user_handle)},
        prf_input, True, [row.credential_id for row in existing],
    )


def finish_add_passkey(body: dict) -> ActionResult:
    enforce_rate("auth_verify")
    expect_fields(body, {"credential", "envelope", "label"})
    user, session_row, _current = load_login()
    require_reauth(session_row)
    challenge = consume_challenge("add_passkey", user_id=user.id)
    credential = _credential(body)
    verified = _verify_registration(credential, challenge.challenge)
    if db.session.query(PasskeyCredential).filter_by(credential_id=verified.credential_id).first():
        fail("credential_exists", 409, "このパスキーはすでに登録されています。", actor_id=user.id)
    data_key = db.session.get(UserDataKey, challenge.request_options["data_key_id"])
    if data_key is None or data_key.user_id != user.id or data_key.retired_at is not None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。", actor_id=user.id)
    wrapped = _envelope(body, challenge.request_options["prf_input"])
    passkey = _save_passkey(user, verified, _label(body), _transports(credential), proven=False)
    db.session.add(UserKeyEnvelope(
        user_id=user.id, data_key_id=data_key.id, passkey_id=passkey.id,
        kdf_version=1, crypto_version=CRYPTO_VERSION, **wrapped,
    ))
    audit("passkey_added", "passkey", passkey.id, "success", actor_id=user.id)
    commit_or_conflict()
    return ActionResult({"passkey_id": passkey.id, "data_key_id": data_key.id})


def begin_passkey_proof(passkey_id: str) -> ActionResult:
    user, session_row, _current = load_login()
    require_reauth(session_row)
    passkey = db.session.get(PasskeyCredential, passkey_id)
    if passkey is None or passkey.user_id != user.id or passkey.revoked_at is not None:
        fail("not_found", 404, "パスキーが見つかりません。", actor_id=user.id)
    wrapped = _envelope_for(passkey)
    if wrapped is None:
        fail("key_unavailable", 409, "このパスキーで解除できる鍵が見つかりません。", actor_id=user.id)
    result = _start_challenge(
        "prove_passkey", user.id, {"passkey_id": passkey.id}, wrapped.prf_input, False, [passkey.credential_id],
    )
    result.body["envelope"] = envelope_view(wrapped)
    return result


def finish_passkey_proof(passkey_id: str, body: dict) -> dict:
    enforce_rate("auth_verify")
    expect_fields(body, {"credential"})
    user, session_row, _current = load_login()
    require_reauth(session_row)
    passkey = db.session.get(PasskeyCredential, passkey_id)
    if passkey is None or passkey.user_id != user.id or passkey.revoked_at is not None:
        fail("not_found", 404, "パスキーが見つかりません。", actor_id=user.id)
    challenge = consume_challenge("prove_passkey", user_id=user.id)
    if challenge.request_options.get("passkey_id") != passkey.id:
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。", actor_id=user.id)
    credential = _credential(body)
    _user_handle_matches(credential, user)
    verified = _verify_assertion(credential, challenge.challenge, passkey.public_key, passkey.sign_count)
    if not _same_bytes(verified.credential_id, passkey.credential_id):
        fail("webauthn_invalid", 400, "パスキーの検証に失敗しました。", actor_id=user.id)
    passkey.sign_count = int(verified.new_sign_count)
    passkey.last_used_at = utcnow()
    passkey.proven_at = utcnow()
    audit("passkey_proved", "passkey", passkey.id, "success", actor_id=user.id)
    db.session.commit()
    return {"ok": True, "passkey_id": passkey.id}


def disable_passkey(passkey_id: str, body: dict) -> ActionResult:
    expect_fields(body, {"confirm_last"})
    user, session_row, current = load_login()
    require_reauth(session_row)
    passkey = db.session.get(PasskeyCredential, passkey_id)
    if passkey is None or passkey.user_id != user.id or passkey.revoked_at is not None:
        fail("not_found", 404, "パスキーが見つかりません。", actor_id=user.id)
    usable = _usable_passkeys(user.id).count()
    if passkey.proven_at is not None and usable <= 1 and body.get("confirm_last") is not True:
        fail("last_passkey", 409, "最後のパスキーです。無効化すると過去のデータを復号できません。確認が必要です。",
             action="passkey_disable", target_type="passkey", target_id=passkey.id, actor_id=user.id)
    now = utcnow()
    passkey.revoked_at = now
    db.session.execute(
        update(AuthSession).where(
            AuthSession.passkey_id == passkey.id, AuthSession.revoked_at.is_(None),
        ).values(revoked_at=now)
    )
    audit("passkey_disabled", "passkey", passkey.id, "success", actor_id=user.id)
    db.session.commit()
    return ActionResult({"ok": True}, clear_session=current.id == passkey.id)


def _active_receive_key(user_id: str) -> UserEncryptionKey | None:
    return (
        db.session.query(UserEncryptionKey)
        .filter_by(user_id=user_id, purpose="receive", status="active")
        .order_by(UserEncryptionKey.created_at.desc())
        .first()
    )


def register_encryption_key(body: dict) -> ActionResult:
    allowed = {"public_key", "replaces", "acknowledge_key_change", "key_bundle"}
    expect_fields(body, allowed)
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    public_key = b64decode(body.get("public_key"), exact=32)
    current = _active_receive_key(user.id)
    now = utcnow()
    if current is not None:
        if body.get("replaces") != current.id or body.get("acknowledge_key_change") is not True:
            fail("key_confirmation_required", 409,
                 "受信用の公開鍵を置き換えるには、現在の鍵IDを確認して明示してください。",
                 action="encryption_key_rotate", target_type="encryption_key", target_id=current.id,
                 actor_id=user.id)
        current.status = "retired"
        current.retired_at = now
        db.session.execute(
            update(FamilyShare).where(
                FamilyShare.recipient_key_id == current.id,
                FamilyShare.status == "active",
            ).values(status="key_pending")
        )
    row = UserEncryptionKey(user_id=user.id, purpose="receive", public_key=public_key, status="active")
    db.session.add(row)
    if body.get("key_bundle") is not None:
        data_key = _current_data_key(user.id)
        ciphertext, nonce = _bundle(body)
        previous = latest_bundle(user.id, data_key.id)
        revision = 1 if previous is None else previous.revision + 1
        db.session.add(UserKeyBundle(
            user_id=user.id, data_key_id=data_key.id, revision=revision,
            ciphertext=ciphertext, nonce=nonce, crypto_version=CRYPTO_VERSION,
        ))
    audit("encryption_key_registered", "encryption_key", row.id, "success", actor_id=user.id)
    commit_or_conflict()
    return ActionResult({"encryption_key_id": row.id, "replaced": None if current is None else current.id})


def _connected(user_id: str, other_id: str) -> bool:
    low, high = sorted((user_id, other_id))
    return db.session.query(FamilyConnection).filter_by(
        user_low_id=low, user_high_id=high, status="active",
    ).first() is not None


def _key_view(row: UserEncryptionKey) -> dict:
    return {
        "id": row.id,
        "purpose": row.purpose,
        "status": row.status,
        "public_key": b64encode(row.public_key),
        "created_at": iso(row.created_at),
        "retired_at": iso(row.retired_at),
    }


def list_encryption_keys(owner_id: str) -> dict:
    user, _session, _passkey = load_login()
    if owner_id == user.id:
        rows = db.session.query(UserEncryptionKey).filter_by(user_id=user.id).order_by(UserEncryptionKey.created_at.asc()).all()
        return {"user_id": owner_id, "keys": [_key_view(row) for row in rows]}
    if not _connected(user.id, owner_id):
        fail("not_found", 404, "公開鍵が見つかりません。")
    current = _active_receive_key(owner_id)
    if current is None:
        fail("not_found", 404, "公開鍵が見つかりません。")
    return {"user_id": owner_id, "keys": [_key_view(current)]}


def save_passkey_for_migration(user: User, verified, label: str | None, transports: list[str],
                               wrapped: dict, data_key_id: str) -> PasskeyCredential:
    data_key = db.session.get(UserDataKey, data_key_id)
    if data_key is None or data_key.user_id != user.id or data_key.retired_at is not None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。", actor_id=user.id)
    passkey = _save_passkey(user, verified, label, transports)
    db.session.add(UserKeyEnvelope(
        user_id=user.id, data_key_id=data_key.id, passkey_id=passkey.id,
        kdf_version=1, crypto_version=CRYPTO_VERSION, **wrapped,
    ))
    return passkey
