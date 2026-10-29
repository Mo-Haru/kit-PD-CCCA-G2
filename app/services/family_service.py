"""家族の招待、接続、受信者ごとの共有。サーバーは再暗号化しない。"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import or_, update
from sqlalchemy.exc import IntegrityError

from app.contact_hash import HASH_VERSION, ContactHashKeyError, address_hmac
from app.crypto_contract import CRYPTO_VERSION, HPKE_SUITE
from app.extensions import db
from app.models import (
    FamilyConnection,
    FamilyInvitation,
    FamilyShare,
    FamilyShareOwnerCopy,
    FamilyShareVersion,
    User,
    UserDataKey,
    UserEncryptionKey,
    utcnow,
)
from app.services.share_quota import admit_new_share, require_capacity_to_revive
from app.services.support import (
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

OPEN_FIELDS = {"kind"}
EMAIL_FIELDS = {"kind", "email"}
SHARE_FIELDS = {"id", "connection_id", "recipient_key_id", "expires_at", "confirmed", "version", "owner_copy"}
UPDATE_FIELDS = {"expected_version", "recipient_key_id", "confirmed", "version", "owner_copy"}


def _pair(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _active_connection_between(left: str, right: str) -> FamilyConnection | None:
    low, high = _pair(left, right)
    return db.session.query(FamilyConnection).filter_by(
        user_low_id=low, user_high_id=high, status="active",
    ).one_or_none()


def _invitation_view(row: FamilyInvitation, viewer_id: str) -> dict:
    recipient = db.session.get(User, row.recipient_user_id) if row.recipient_user_id else None
    sender = db.session.get(User, row.sender_user_id)
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "expires_at": iso(row.expires_at),
        "created_at": iso(row.created_at),
        "sender_display_name": None if sender is None else sender.display_name,
        "recipient_display_name": None if recipient is None else recipient.display_name,
        "incoming": row.sender_user_id != viewer_id,
    }


def _expire_invitation(row: FamilyInvitation) -> None:
    if row.status in {"pending", "awaiting_sender"} and row.expires_at <= utcnow():
        row.status = "expired"
        row.resolved_at = utcnow()


def create_invitation(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("invite")
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    kind = body.get("kind")
    if kind == "open":
        expect_fields(body, OPEN_FIELDS)
        address = None
    elif kind == "email":
        expect_fields(body, EMAIL_FIELDS)
        # 登録の有無で応答を分けない。未登録でも接続は作らず、同じ形の招待だけを返す。
        address = _email_hash(body.get("email"))
    else:
        fail("invalid_invitation", 400, "招待の種類が正しくありません。", actor_id=user.id)
    pending = db.session.query(FamilyInvitation).filter(
        FamilyInvitation.sender_user_id == user.id,
        FamilyInvitation.status.in_(("pending", "awaiting_sender")),
    ).count()
    if pending >= 20:
        fail("invitation_limit", 400, "未完了の招待が多すぎます。", actor_id=user.id)
    now = utcnow()
    token = secrets.token_bytes(32)
    row = FamilyInvitation(
        sender_user_id=user.id,
        kind=kind,
        token_hash=hashlib.sha256(token).digest(),
        address_hash=address,
        address_hash_version=None if address is None else HASH_VERSION,
        status="pending",
        expires_at=now + timedelta(seconds=int(current_app.config["INVITATION_TTL_SECONDS"])),
        created_at=now,
    )
    db.session.add(row)
    audit("invitation_created", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {
        "invitation": _invitation_view(row, user.id),
        "token": b64encode(token),
        "invite_path": "/family/invite",
    }


def _email_hash(value) -> bytes:
    try:
        return address_hmac(value)
    except ValueError:
        fail("invalid_email", 400, "メールアドレスの形式が正しくありません。")
    except ContactHashKeyError:
        fail("contact_hash_unconfigured", 503, "メール照合の設定が未完了です。")


def _owner_of_email(digest: bytes) -> str | None:
    from app.models import VerifiedContact
    row = db.session.query(VerifiedContact).filter_by(
        kind="email", address_hash=digest, address_hash_version=HASH_VERSION,
    ).one_or_none()
    return None if row is None else row.user_id


def list_invitations() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(FamilyInvitation).filter(
        or_(FamilyInvitation.sender_user_id == user.id, FamilyInvitation.recipient_user_id == user.id)
    ).order_by(FamilyInvitation.created_at.desc()).limit(100).all()
    changed = False
    for row in rows:
        before = row.status
        _expire_invitation(row)
        changed = changed or row.status != before
    if changed:
        db.session.commit()
    return {"invitations": [_invitation_view(row, user.id) for row in rows]}


def _by_token(token: str) -> FamilyInvitation:
    try:
        raw = b64decode(token, exact=32)
    except Exception:
        fail("invitation_invalid", 400, "招待を確認できません。")
    row = db.session.query(FamilyInvitation).filter_by(token_hash=hashlib.sha256(raw).digest()).one_or_none()
    if row is None:
        fail("invitation_invalid", 400, "招待を確認できません。")
    _expire_invitation(row)
    return row


def request_open_invitation(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("invite")
    expect_fields(body, {"token"})
    user, _session, _passkey = load_login()
    row = _by_token(body.get("token"))
    if row.kind != "open" or row.status != "pending":
        fail("invitation_invalid", 400, "この招待は使えません。", actor_id=user.id)
    if row.sender_user_id == user.id:
        fail("invitation_invalid", 400, "この招待は使えません。", actor_id=user.id)
    row.status = "awaiting_sender"
    row.recipient_user_id = user.id
    audit("invitation_requested", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"invitation": _invitation_view(row, user.id)}


def accept_email_invitation(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("invite")
    expect_fields(body, {"token"})
    user, _session, _passkey = load_login()
    row = _by_token(body.get("token"))
    if row.kind != "email" or row.status != "pending" or row.address_hash is None:
        fail("invitation_invalid", 400, "この招待は使えません。", actor_id=user.id)
    owner_id = _owner_of_email(row.address_hash)
    if owner_id is None or owner_id != user.id or row.sender_user_id == user.id:
        fail("recipient_mismatch", 403, "この招待を受けられるアカウントではありません。",
             action="invitation_accept", target_type="invitation", target_id=row.id, actor_id=user.id)
    connection = _connect(row, user.id)
    audit("invitation_accepted", "connection", connection.id, "success", actor_id=user.id)
    db.session.commit()
    return {"connection_id": connection.id}


def confirm_invitation(invitation_id: str) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    row = db.session.get(FamilyInvitation, invitation_id)
    if row is None or row.sender_user_id != user.id:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    _expire_invitation(row)
    if row.status != "awaiting_sender" or row.recipient_user_id is None:
        fail("invitation_invalid", 400, "承認待ちの招待ではありません。", actor_id=user.id)
    connection = _connect(row, row.recipient_user_id)
    audit("invitation_confirmed", "connection", connection.id, "success", actor_id=user.id)
    db.session.commit()
    return {"connection_id": connection.id}


def _connect(row: FamilyInvitation, recipient_id: str) -> FamilyConnection:
    if recipient_id == row.sender_user_id:
        fail("invitation_invalid", 400, "この招待は使えません。")
    if _active_connection_between(row.sender_user_id, recipient_id) is not None:
        fail("already_connected", 409, "すでに接続されています。")
    now = utcnow()
    row.status = "accepted"
    row.recipient_user_id = recipient_id
    row.resolved_at = now
    low, high = _pair(row.sender_user_id, recipient_id)
    connection = FamilyConnection(
        invitation_id=row.id,
        user_low_id=low,
        user_high_id=high,
        status="active",
        connected_at=now,
    )
    db.session.add(connection)
    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        fail("invitation_invalid", 409, "この招待は使えません。")
        raise exc
    return connection


def reject_invitation(invitation_id: str) -> dict:
    user, _session, _passkey = load_login()
    row = db.session.get(FamilyInvitation, invitation_id)
    if row is None:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    _expire_invitation(row)
    if row.sender_user_id == user.id and row.status in {"pending", "awaiting_sender"}:
        row.status = "cancelled"
    elif row.recipient_user_id == user.id and row.status == "awaiting_sender":
        row.status = "rejected"
    else:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    row.resolved_at = utcnow()
    audit("invitation_closed", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"ok": True, "status": row.status}


def list_connections() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(FamilyConnection).filter(
        or_(FamilyConnection.user_low_id == user.id, FamilyConnection.user_high_id == user.id)
    ).order_by(FamilyConnection.connected_at.desc()).limit(100).all()
    return {"connections": [_connection_view(row, user.id) for row in rows]}


def _connection_view(row: FamilyConnection, viewer_id: str) -> dict:
    other_id = row.user_high_id if row.user_low_id == viewer_id else row.user_low_id
    other = db.session.get(User, other_id)
    return {
        "id": row.id,
        "status": row.status,
        "other_user_id": other_id,
        "other_display_name": None if other is None else other.display_name,
        "connected_at": iso(row.connected_at),
        "disconnected_at": iso(row.disconnected_at),
    }


def disconnect(connection_id: str) -> dict:
    user, _session, _passkey = load_login()
    row = db.session.get(FamilyConnection, connection_id)
    if row is None or user.id not in {row.user_low_id, row.user_high_id}:
        fail("not_found", 404, "接続が見つかりません。", actor_id=user.id)
    if row.status != "active":
        return {"ok": True, "status": row.status}
    now = utcnow()
    row.status = "disconnected"
    row.disconnected_at = now
    row.disconnected_by_user_id = user.id
    db.session.execute(
        update(FamilyShare).where(
            FamilyShare.connection_id == row.id,
            FamilyShare.status != "stopped",
        ).values(status="stopped", stopped_at=now)
    )
    audit("connection_disconnected", "connection", row.id, "success", actor_id=user.id)
    db.session.commit()
    return {"ok": True, "status": "disconnected"}


def _active_key(user_id: str) -> UserEncryptionKey | None:
    return (
        db.session.query(UserEncryptionKey)
        .filter_by(user_id=user_id, purpose="receive", status="active")
        .order_by(UserEncryptionKey.created_at.desc())
        .first()
    )


def _hpke_parts(version: dict) -> tuple[bytes, bytes]:
    if not isinstance(version, dict):
        fail("invalid_ciphertext", 400, "共有データの形式が正しくありません。")
    expect_fields(version, {"hpke_enc", "ciphertext"})
    return (
        b64decode(version.get("hpke_enc"), exact=32),
        b64decode(version.get("ciphertext"), minimum=16, maximum=ciphertext_limit()),
    )


def _owner_copy(body: dict, owner_id: str) -> tuple[UserDataKey, bytes, bytes]:
    copy = body.get("owner_copy")
    if not isinstance(copy, dict):
        fail("invalid_ciphertext", 400, "所有者用の履歴データが必要です。")
    expect_fields(copy, {"data_key_id", "nonce", "ciphertext", "crypto_version"})
    if copy.get("crypto_version") != CRYPTO_VERSION:
        fail("unsupported_crypto", 400, "未対応の暗号方式です。")
    data_key = db.session.get(UserDataKey, copy.get("data_key_id"))
    if data_key is None or data_key.user_id != owner_id or data_key.retired_at is not None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。")
    return data_key, b64decode(copy.get("nonce"), exact=12), b64decode(
        copy.get("ciphertext"), minimum=16, maximum=ciphertext_limit(),
    )


def _require_confirmed(body: dict) -> None:
    if body.get("confirmed") is not True:
        fail("confirmation_required", 400, "共有内容を確認してから確定してください。")


def _parse_expiry(value, created):
    if value is None:
        return None
    expires = parse_iso(value)
    if expires <= created or expires > created + timedelta(seconds=int(current_app.config["FAMILY_SHARE_MAX_SECONDS"])):
        fail("invalid_time", 400, "共有期限は、今から設定できる範囲で指定してください。")
    return expires


def create_share(body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, SHARE_FIELDS)
    _require_confirmed(body)
    admit_new_share(user.id)
    try:
        share_id = str(uuid.UUID(body.get("id")))
    except (TypeError, ValueError):
        fail("invalid_id", 400, "共有IDが正しくありません。", actor_id=user.id)
    connection = db.session.get(FamilyConnection, body.get("connection_id"))
    if connection is None or connection.status != "active" or user.id not in {connection.user_low_id, connection.user_high_id}:
        fail("not_found", 404, "接続が見つかりません。", actor_id=user.id)
    recipient_id = connection.user_high_id if connection.user_low_id == user.id else connection.user_low_id
    current_key = _active_key(recipient_id)
    if current_key is None:
        fail("key_unavailable", 409, "相手の受信用公開鍵がまだありません。", actor_id=user.id)
    if body.get("recipient_key_id") != current_key.id:
        fail("key_changed", 409, "相手の受信用公開鍵が変わっています。表示を確認してから共有してください。",
             action="share_create", target_type="family_share", actor_id=user.id,
             details={"current_key_id": current_key.id})
    now = utcnow()
    enc, ciphertext = _hpke_parts(body.get("version"))
    data_key, nonce, owner_ct = _owner_copy(body, user.id)
    share = FamilyShare(
        id=share_id,
        owner_user_id=user.id,
        recipient_user_id=recipient_id,
        connection_id=connection.id,
        status="active",
        recipient_key_id=current_key.id,
        expires_at=_parse_expiry(body.get("expires_at"), now),
        current_version=1,
        created_at=now,
    )
    db.session.add(share)
    db.session.add(FamilyShareVersion(
        share_id=share.id, version=1, hpke_enc=enc, ciphertext=ciphertext, crypto_suite=HPKE_SUITE,
    ))
    db.session.add(FamilyShareOwnerCopy(
        share_id=share.id, owner_user_id=user.id, version=1, data_key_id=data_key.id,
        nonce=nonce, ciphertext=owner_ct, crypto_version=CRYPTO_VERSION,
    ))
    audit("share_created", "family_share", share.id, "success", actor_id=user.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        fail("conflict", 409, "共有を作成できませんでした。")
        raise exc
    return {"share": _owner_share_view(share)}


def update_share(share_id: str, body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, UPDATE_FIELDS)
    _require_confirmed(body)
    share = db.session.get(FamilyShare, share_id)
    if share is None or share.owner_user_id != user.id:
        fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
    if share.status != "active" and share.status != "key_pending":
        fail("share_stopped", 409, "停止した共有は更新できません。", actor_id=user.id)
    connection = db.session.get(FamilyConnection, share.connection_id)
    if connection is None or connection.status != "active":
        fail("share_stopped", 409, "接続が解除されているため更新できません。", actor_id=user.id)
    expected = body.get("expected_version")
    if expected != share.current_version:
        fail("revision_conflict", 409, "別の版があるため更新できません。", actor_id=user.id)
    if share.current_version >= int(current_app.config["MAX_SHARE_VERSIONS"]):
        fail("history_limit", 400, "この共有の履歴が上限に達しています。", actor_id=user.id)
    current_key = _active_key(share.recipient_user_id)
    if current_key is None or body.get("recipient_key_id") != current_key.id:
        fail("key_changed", 409, "相手の受信用公開鍵が変わっています。表示を確認してから更新してください。",
             actor_id=user.id,
             details={"current_key_id": None if current_key is None else current_key.id})
    version = share.current_version + 1
    enc, ciphertext = _hpke_parts(body.get("version"))
    data_key, nonce, owner_ct = _owner_copy(body, user.id)
    share.current_version = version
    share.recipient_key_id = current_key.id
    # 新しい鍵向けの暗号文を同じ更新で保存したときだけ、配信可能な状態へ戻す。
    share.status = "active"
    db.session.add(FamilyShareVersion(
        share_id=share.id, version=version, hpke_enc=enc, ciphertext=ciphertext, crypto_suite=HPKE_SUITE,
    ))
    db.session.add(FamilyShareOwnerCopy(
        share_id=share.id, owner_user_id=user.id, version=version, data_key_id=data_key.id,
        nonce=nonce, ciphertext=owner_ct, crypto_version=CRYPTO_VERSION,
    ))
    audit("share_updated", "family_share", share.id, "success", actor_id=user.id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        fail("revision_conflict", 409, "別の版があるため更新できません。")
        raise exc
    return {"share": _owner_share_view(share)}


def change_expiry(share_id: str, body: dict) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    expect_fields(body, {"expires_at"})
    share = db.session.get(FamilyShare, share_id)
    if share is None or share.owner_user_id != user.id:
        fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
    if share.status == "stopped":
        fail("share_stopped", 409, "停止した共有の期限は変更できません。", actor_id=user.id)
    expires = _parse_expiry(body.get("expires_at"), share.created_at)
    require_capacity_to_revive(user.id, share.expires_at, expires)
    share.expires_at = expires
    audit("share_expiry_changed", "family_share", share.id, "success", actor_id=user.id)
    db.session.commit()
    return {"share": _owner_share_view(share)}


def stop_share(share_id: str) -> dict:
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    share = db.session.get(FamilyShare, share_id)
    if share is None or share.owner_user_id != user.id:
        fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
    if share.status != "stopped":
        share.status = "stopped"
        share.stopped_at = utcnow()
        audit("share_stopped", "family_share", share.id, "success", actor_id=user.id)
        db.session.commit()
    return {"ok": True, "status": share.status}


def _version(share_id: str, version: int) -> FamilyShareVersion | None:
    return db.session.query(FamilyShareVersion).filter_by(share_id=share_id, version=version).one_or_none()


def _copy(share_id: str, version: int) -> FamilyShareOwnerCopy | None:
    return db.session.query(FamilyShareOwnerCopy).filter_by(share_id=share_id, version=version).one_or_none()


def _version_view(row: FamilyShareVersion) -> dict:
    return {
        "version": row.version,
        "hpke_enc": b64encode(row.hpke_enc),
        "ciphertext": b64encode(row.ciphertext),
        "crypto_suite": row.crypto_suite,
        "created_at": iso(row.created_at),
    }


def _copy_view(row: FamilyShareOwnerCopy) -> dict:
    return {
        "version": row.version,
        "data_key_id": row.data_key_id,
        "nonce": b64encode(row.nonce),
        "ciphertext": b64encode(row.ciphertext),
        "crypto_version": row.crypto_version,
        "created_at": iso(row.created_at),
    }


def _owner_share_view(share: FamilyShare) -> dict:
    other = db.session.get(User, share.recipient_user_id)
    return {
        "id": share.id,
        "status": share.status,
        "recipient_user_id": share.recipient_user_id,
        "recipient_display_name": None if other is None else other.display_name,
        "recipient_key_id": share.recipient_key_id,
        "connection_id": share.connection_id,
        "current_version": share.current_version,
        "expires_at": iso(share.expires_at),
        "created_at": iso(share.created_at),
        "updated_at": iso(share.updated_at),
        "stopped_at": iso(share.stopped_at),
        "current": _version_view(_version(share.id, share.current_version)),
        "owner_copy": _copy_view(_copy(share.id, share.current_version)),
    }


def list_own_shares() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(FamilyShare).filter_by(owner_user_id=user.id).order_by(FamilyShare.created_at.desc()).limit(100).all()
    return {"shares": [_owner_share_view(row) for row in rows]}


def own_history(share_id: str) -> dict:
    user, _session, _passkey = load_login()
    share = db.session.get(FamilyShare, share_id)
    if share is None or share.owner_user_id != user.id:
        fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
    copies = (
        db.session.query(FamilyShareOwnerCopy)
        .filter_by(share_id=share.id)
        .order_by(FamilyShareOwnerCopy.version.asc())
        .all()
    )
    return {
        "id": share.id,
        "status": share.status,
        "current_version": share.current_version,
        "history": [_copy_view(row) for row in copies],
    }


def _connection_active(share: FamilyShare) -> bool:
    connection = db.session.get(FamilyConnection, share.connection_id)
    return connection is not None and connection.status == "active"


def _expired(share: FamilyShare) -> bool:
    return share.expires_at is not None and share.expires_at <= utcnow()


def _current_recipient_key(share: FamilyShare) -> bool:
    key = db.session.get(UserEncryptionKey, share.recipient_key_id)
    if key is None or key.user_id != share.recipient_user_id or key.status != "active" or key.purpose != "receive":
        return False
    current = _active_key(share.recipient_user_id)
    return current is not None and current.id == key.id


def _deliverable(share: FamilyShare, user_id: str) -> bool:
    """受信者へ暗号文を返してよい共有。active かつ接続・期限・鍵が現在有効なときだけ。"""
    return (
        share.recipient_user_id == user_id
        and share.status == "active"
        and not _expired(share)
        and _connection_active(share)
        and _current_recipient_key(share)
    )


def _pending_notice(share: FamilyShare, user_id: str) -> bool:
    """鍵の更新待ち。暗号文は返さないが、状態だけは受信者に見せる。"""
    return (
        share.recipient_user_id == user_id
        and share.status == "key_pending"
        and not _expired(share)
        and _connection_active(share)
    )


def _received_view(share: FamilyShare, *, include_ciphertext: bool) -> dict:
    owner = db.session.get(User, share.owner_user_id)
    view = {
        "id": share.id,
        "status": share.status,
        "owner_user_id": share.owner_user_id,
        "owner_display_name": None if owner is None else owner.display_name,
        "current_version": share.current_version,
        "expires_at": iso(share.expires_at),
        "updated_at": iso(share.updated_at),
    }
    if include_ciphertext:
        view["recipient_key_id"] = share.recipient_key_id
        view["current"] = _version_view(_version(share.id, share.current_version))
    return view


def list_received() -> dict:
    user, _session, _passkey = load_login()
    rows = db.session.query(FamilyShare).filter_by(recipient_user_id=user.id).order_by(FamilyShare.created_at.desc()).limit(100).all()
    shares = []
    for row in rows:
        if _deliverable(row, user.id):
            shares.append(_received_view(row, include_ciphertext=True))
        elif _pending_notice(row, user.id):
            shares.append(_received_view(row, include_ciphertext=False))
    return {"shares": shares}


def get_received(share_id: str) -> dict:
    user, _session, _passkey = load_login()
    share = db.session.get(FamilyShare, share_id)
    if share is None or share.recipient_user_id != user.id:
        fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
    if _deliverable(share, user.id):
        return {"share": _received_view(share, include_ciphertext=True)}
    if _pending_notice(share, user.id):
        return {"share": _received_view(share, include_ciphertext=False)}
    fail("not_found", 404, "共有が見つかりません。", actor_id=user.id)
