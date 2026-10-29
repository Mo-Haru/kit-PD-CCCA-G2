"""家族の招待、接続、受信者ごとの共有。サーバーは再暗号化しない。"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import timedelta

from flask import current_app
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

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
# 紛らわしい 0/O、1/I/L を除いた31文字。8文字で約39ビット。
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
CONFIRM_PATH = "/family/confirm"
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


OPEN_STATES = ("pending", "awaiting_sender")


def _transition(invitation_id: str, *conditions, **values) -> bool:
    """招待の状態変化は、すべて条件付きUPDATE1回で行う。条件に合った1件だけが変わる。

    読んでから書くと、同時の承認・再発行・取り消しが互いの結果を上書きするため。
    PostgreSQLは行ロックと条件の再評価、SQLiteは書き込みロックで直列になる。
    """
    changed = db.session.execute(
        update(FamilyInvitation).where(FamilyInvitation.id == invitation_id, *conditions).values(**values)
        .execution_options(synchronize_session=False)
    ).rowcount
    return changed == 1


def _expire_invitation(row: FamilyInvitation) -> None:
    if row.status in OPEN_STATES and row.expires_at <= utcnow():
        now = utcnow()
        _transition(row.id, FamilyInvitation.status.in_(OPEN_STATES), FamilyInvitation.expires_at <= now,
                    status="expired", resolved_at=now)
        db.session.refresh(row)


def create_invitation(body: dict) -> dict:
    from app.services.support import enforce_rate
    enforce_rate("invite")
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    # メール招待は相手のアドレスを知る必要があり、照合値もサーバーに残るため受け付けない。
    if body.get("kind") != "open":
        fail("invalid_invitation", 400, "招待の種類が正しくありません。", actor_id=user.id)
    expect_fields(body, OPEN_FIELDS)
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
        kind="open",
        token_hash=hashlib.sha256(token).digest(),
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


def _code_hash(invitation_id: str, code: str) -> bytes:
    return hashlib.sha256(f"acp/family-confirm-code/v1|{invitation_id}|{code}".encode()).digest()


def _normalize_code(value) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.replace("-", "").replace(" ", "").upper()
    if len(code) != CODE_LENGTH or any(char not in CODE_ALPHABET for char in code):
        return None
    return code


def _new_confirmation(row: FamilyInvitation) -> tuple[dict, dict]:
    """申請した人だけに返す確認情報と、保存するハッシュ。前の確認情報は、保存した時点で使えなくなる。"""
    token = secrets.token_bytes(32)
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    stored = {
        "confirm_token_hash": hashlib.sha256(token).digest(),
        "confirm_code_hash": _code_hash(row.id, code),
        "confirm_failures": 0,
    }
    return stored, {
        "token": b64encode(token),
        "code": f"{code[:4]}-{code[4:]}",
        "confirm_path": CONFIRM_PATH,
        "expires_at": iso(row.expires_at),
    }


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
    stored, confirmation = _new_confirmation(row)
    # 同じ招待へ同時に申請されても、申請者になれるのは1人だけ。
    if not _transition(row.id, FamilyInvitation.status == "pending", FamilyInvitation.expires_at > utcnow(),
                       status="awaiting_sender", recipient_user_id=user.id, **stored):
        fail("invitation_invalid", 400, "この招待は使えません。", actor_id=user.id)
    audit("invitation_requested", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    db.session.refresh(row)
    return {"invitation": _invitation_view(row, user.id), "confirmation": confirmation}


def reissue_confirmation(invitation_id: str) -> dict:
    """申請した人が確認リンクとコードを作り直す。画面を閉じた後や、コードを間違えられた後に使う。"""
    from app.services.support import enforce_rate
    enforce_rate("invite")
    user, _session, _passkey = load_login()
    row = db.session.get(FamilyInvitation, invitation_id)
    if row is None or row.recipient_user_id != user.id:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    stored, confirmation = _new_confirmation(row)
    # 承認・取り消しと同時でも、まだ確認待ちのときだけ差し替える。
    if not _transition(row.id, FamilyInvitation.recipient_user_id == user.id,
                       FamilyInvitation.status == "awaiting_sender", FamilyInvitation.expires_at > utcnow(),
                       **stored):
        db.session.rollback()
        _expire_invitation(row)
        db.session.commit()
        fail("invitation_invalid", 400, "確認待ちの申請ではありません。", actor_id=user.id)
    audit("invitation_confirmation_issued", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    db.session.refresh(row)
    return {"invitation": _invitation_view(row, user.id), "confirmation": confirmation}


def _explain_unconfirmable(row: FamilyInvitation | None, user_id: str):
    """承認の条件付き更新が当たらなかった理由を、最新の行から返す。"""
    if row is None or row.sender_user_id != user_id:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user_id)
    db.session.refresh(row)
    _expire_invitation(row)
    db.session.commit()
    if row.status == "cancelled" and row.confirm_failures >= int(current_app.config["FAMILY_CONFIRM_MAX_FAILURES"]):
        fail("confirmation_locked", 409,
             "確認コードを続けて間違えたため、この申請を無効にしました。招待からやり直してください。",
             actor_id=user_id)
    if row.status != "awaiting_sender" or row.recipient_user_id is None:
        fail("invitation_invalid", 400, "承認待ちの招待ではありません。", actor_id=user_id)
    if row.confirm_token_hash is None or row.confirm_code_hash is None:
        fail("confirmation_required", 409,
             "この申請には確認コードがありません。申請した家族に、家族画面で確認コードを再発行してもらってください。",
             actor_id=user_id)


def _accept(row: FamilyInvitation, user_id: str, proof) -> dict | None:
    """確認情報が今も一致し、確認待ちのときだけ承認済みにし、同じトランザクションで接続を作る。

    proofは確認コードまたは確認リンクのハッシュの一致条件。再発行・取り消しと同時でも、どちらか一方だけが成功する。
    """
    now = utcnow()
    recipient_id = row.recipient_user_id
    if recipient_id is None or not _transition(
        row.id, FamilyInvitation.sender_user_id == user_id, FamilyInvitation.status == "awaiting_sender",
        FamilyInvitation.recipient_user_id == recipient_id, FamilyInvitation.expires_at > now, proof,
        status="accepted", resolved_at=now, confirm_token_hash=None, confirm_code_hash=None,
    ):
        db.session.rollback()
        return None
    if recipient_id == user_id:
        fail("invitation_invalid", 400, "この招待は使えません。")
    if _active_connection_between(user_id, recipient_id) is not None:
        fail("already_connected", 409, "すでに接続されています。")
    low, high = _pair(user_id, recipient_id)
    connection = FamilyConnection(invitation_id=row.id, user_low_id=low, user_high_id=high,
                                  status="active", connected_at=now)
    db.session.add(connection)
    audit("invitation_confirmed", "connection", connection.id, "success", actor_id=user_id)
    try:
        db.session.commit()
    except IntegrityError as exc:
        db.session.rollback()
        fail("invitation_invalid", 409, "この招待は使えません。")
        raise exc
    recipient = db.session.get(User, recipient_id)
    return {"connection_id": connection.id,
            "recipient_display_name": None if recipient is None else recipient.display_name}


def _count_failure(row: FamilyInvitation, user_id: str) -> None:
    """誤入力を、確認待ちで上限未満のときだけDB上で1つ加算する。上限に達したらその場で申請を無効にする。"""
    limit = int(current_app.config["FAMILY_CONFIRM_MAX_FAILURES"])
    counted = _transition(
        row.id, FamilyInvitation.sender_user_id == user_id, FamilyInvitation.status == "awaiting_sender",
        FamilyInvitation.confirm_code_hash.is_not(None), FamilyInvitation.confirm_failures < limit,
        FamilyInvitation.expires_at > utcnow(),
        confirm_failures=FamilyInvitation.confirm_failures + 1,
    )
    if not counted:
        db.session.rollback()
        _explain_unconfirmable(row, user_id)
        fail("invitation_invalid", 400, "承認待ちの招待ではありません。", actor_id=user_id)
    # 加算した行は、このトランザクションがコミットするまで他から書き換えられない。
    failures = db.session.execute(
        select(FamilyInvitation.confirm_failures).where(FamilyInvitation.id == row.id)
    ).scalar_one()
    if failures >= limit:
        _transition(row.id, FamilyInvitation.status == "awaiting_sender",
                    status="cancelled", resolved_at=utcnow(), confirm_token_hash=None, confirm_code_hash=None)
        audit("invitation_confirmation_locked", "invitation", row.id, "failure", "confirmation_locked", user_id)
        db.session.commit()
        fail("confirmation_locked", 409,
             "確認コードを続けて間違えたため、この申請を無効にしました。招待からやり直してください。",
             actor_id=user_id)
    remaining = limit - failures
    audit("invitation_confirmation_mismatch", "invitation", row.id, "failure", "confirmation_mismatch", user_id)
    db.session.commit()
    fail("confirmation_mismatch", 400,
         f"確認コードが一致しません。あと{remaining}回間違えると、この申請は無効になります。",
         actor_id=user_id, details={"remaining": remaining})


def confirm_invitation(invitation_id: str, body: dict) -> dict:
    """招待した人が、申請した家族から受け取った確認コードで承認する。"""
    expect_fields(body, {"code"})
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    row = db.session.get(FamilyInvitation, invitation_id)
    if row is None or row.sender_user_id != user.id:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    code = _normalize_code(body.get("code"))
    if code is not None:
        # 一致の確認も更新の条件に入れる。読んだ時点の値と比べると、再発行後の古いコードで承認できてしまう。
        accepted = _accept(row, user.id, FamilyInvitation.confirm_code_hash == _code_hash(row.id, code))
        if accepted is not None:
            return accepted
    _count_failure(row, user.id)


def confirm_invitation_link(body: dict) -> dict:
    """招待した人が、申請した家族から受け取った確認リンクを開いて承認する。"""
    from app.services.support import enforce_rate
    enforce_rate("invite")
    expect_fields(body, {"token"})
    user, session_row, _passkey = load_login()
    require_reauth(session_row)
    try:
        raw = b64decode(body.get("token"), exact=32)
    except Exception:
        fail("invitation_invalid", 400, "確認リンクを確認できません。", actor_id=user.id)
    token_hash = hashlib.sha256(raw).digest()
    row = db.session.query(FamilyInvitation).filter_by(confirm_token_hash=token_hash).one_or_none()
    # 別のアカウントで開いた場合も、招待の有無を区別しない。
    if row is None or row.sender_user_id != user.id:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    accepted = _accept(row, user.id, FamilyInvitation.confirm_token_hash == token_hash)
    if accepted is None:
        # 読んだ後に再発行・承認・取り消しされた。差し替え済みのリンクは見つからない扱いにする。
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    return accepted


def reject_invitation(invitation_id: str) -> dict:
    user, _session, _passkey = load_login()
    row = db.session.get(FamilyInvitation, invitation_id)
    if row is None:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    _expire_invitation(row)
    now = utcnow()
    # 承認と同時でも、まだ開いている招待だけを閉じる。承認済みを上書きしない。
    if row.sender_user_id == user.id:
        closed = _transition(row.id, FamilyInvitation.status.in_(OPEN_STATES),
                             status="cancelled", resolved_at=now, confirm_token_hash=None, confirm_code_hash=None)
    elif row.recipient_user_id == user.id:
        closed = _transition(row.id, FamilyInvitation.status == "awaiting_sender",
                             FamilyInvitation.recipient_user_id == user.id,
                             status="rejected", resolved_at=now, confirm_token_hash=None, confirm_code_hash=None)
    else:
        closed = False
    if not closed:
        fail("not_found", 404, "招待が見つかりません。", actor_id=user.id)
    audit("invitation_closed", "invitation", row.id, "success", actor_id=user.id)
    db.session.commit()
    db.session.refresh(row)
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
    share.expires_at = _parse_expiry(body.get("expires_at"), share.created_at)
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
