"""招待した人は、申請した家族から受け取った確認コードかリンクでだけ承認できる。"""

import hashlib

from sqlalchemy import inspect, text

from app.extensions import db
from app.models import AuditLog, FamilyConnection, FamilyInvitation
from app.schema_upgrade import _add_invitation_confirmation, apply_schema_upgrades
from tests.test_protection import post, register


def setup(app):
    with app.app_context():
        db.create_all()
    owner, recipient, outsider = app.test_client(), app.test_client(), app.test_client()
    register(owner, "招待する人")
    _passkey, recipient_body, _public = register(recipient, "申請する人")
    register(outsider, "第三者")
    invited = post(owner, "/api/v1/family/invitations", {"kind": "open"}).get_json()
    requested = post(recipient, "/api/v1/family/invitations/request", {"token": invited["token"]})
    assert requested.status_code == 200, requested.get_json()
    return owner, recipient, outsider, invited["invitation"]["id"], requested.get_json()["confirmation"]


def connections(app):
    with app.app_context():
        return db.session.query(FamilyConnection).count()


def test_request_returns_confirmation_only_to_the_requester(app):
    owner, recipient, _outsider, invitation_id, confirmation = setup(app)
    assert set(confirmation) == {"token", "code", "confirm_path", "expires_at"}
    assert confirmation["confirm_path"] == "/family/confirm"
    assert len(confirmation["code"]) == 9 and confirmation["code"][4] == "-"
    for client in (owner, recipient):
        listed = client.get("/api/v1/family/invitations").get_data(as_text=True)
        assert confirmation["token"] not in listed
        assert confirmation["code"] not in listed and confirmation["code"].replace("-", "") not in listed
    with app.app_context():
        row = db.session.get(FamilyInvitation, invitation_id)
        assert row.status == "awaiting_sender"
        assert row.confirm_code_hash is not None
        assert confirmation["code"].encode() not in row.confirm_code_hash


def test_sender_cannot_confirm_without_the_code(app):
    owner, _recipient, _outsider, invitation_id, confirmation = setup(app)
    for body in ({}, {"code": ""}):
        assert post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", body).status_code == 400
    assert connections(app) == 0
    # 大文字小文字・区切りの有無は問わない。
    code = confirmation["code"].replace("-", " ").lower()
    confirmed = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": code})
    assert confirmed.status_code == 200, confirmed.get_json()
    assert connections(app) == 1
    again = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": confirmation["code"]})
    assert again.status_code == 400


def test_wrong_codes_lock_the_request(app):
    owner, recipient, _outsider, invitation_id, confirmation = setup(app)
    wrong = "AAAA-AAAA" if confirmation["code"] != "AAAA-AAAA" else "BBBB-BBBB"
    for remaining in (4, 3, 2, 1):
        failed = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": wrong})
        assert failed.status_code == 400
        assert failed.get_json()["error"] == "confirmation_mismatch"
        assert f"あと{remaining}回" in failed.get_json()["message"]
        assert failed.get_json()["remaining"] == remaining
    locked = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": wrong})
    assert locked.status_code == 409
    assert locked.get_json()["error"] == "confirmation_locked"
    # 無効にした後は、正しいコードでも承認できない。
    late = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": confirmation["code"]})
    assert late.status_code == 409
    assert late.get_json()["error"] == "confirmation_locked"
    assert post(owner, "/api/v1/family/invitations/confirm-link", {"token": confirmation["token"]}).status_code in {400, 404}
    assert post(recipient, f"/api/v1/family/invitations/{invitation_id}/confirmation").status_code == 400
    assert connections(app) == 0
    with app.app_context():
        row = db.session.get(FamilyInvitation, invitation_id)
        assert row.status == "cancelled"
        assert row.confirm_code_hash is None and row.confirm_token_hash is None
        actions = [entry.action for entry in db.session.query(AuditLog).all()]
        assert actions.count("invitation_confirmation_mismatch") == 4
        assert "invitation_confirmation_locked" in actions


def test_confirmation_link_works_only_for_the_sender(app):
    owner, recipient, outsider, _invitation_id, confirmation = setup(app)
    for client in (recipient, outsider):
        denied = post(client, "/api/v1/family/invitations/confirm-link", {"token": confirmation["token"]})
        assert denied.status_code == 404
    assert post(owner, "/api/v1/family/invitations/confirm-link", {"token": "x" * 43}).status_code == 404
    confirmed = post(owner, "/api/v1/family/invitations/confirm-link", {"token": confirmation["token"]})
    assert confirmed.status_code == 200, confirmed.get_json()
    assert connections(app) == 1
    assert post(owner, "/api/v1/family/invitations/confirm-link", {"token": confirmation["token"]}).status_code == 404


def test_reissue_replaces_the_previous_code_and_link(app):
    owner, recipient, outsider, invitation_id, first = setup(app)
    assert post(owner, f"/api/v1/family/invitations/{invitation_id}/confirmation").status_code == 404
    assert post(outsider, f"/api/v1/family/invitations/{invitation_id}/confirmation").status_code == 404
    reissued = post(recipient, f"/api/v1/family/invitations/{invitation_id}/confirmation")
    assert reissued.status_code == 200, reissued.get_json()
    second = reissued.get_json()["confirmation"]
    assert second["token"] != first["token"]
    assert post(owner, "/api/v1/family/invitations/confirm-link", {"token": first["token"]}).status_code == 404
    if first["code"] != second["code"]:
        stale = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": first["code"]})
        assert stale.get_json()["error"] == "confirmation_mismatch"
    confirmed = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": second["code"]})
    assert confirmed.status_code == 200, confirmed.get_json()


def test_requests_from_before_the_upgrade_need_a_reissued_code(app):
    owner, recipient, _outsider, invitation_id, _confirmation = setup(app)
    with app.app_context():
        row = db.session.get(FamilyInvitation, invitation_id)
        row.confirm_code_hash = None
        row.confirm_token_hash = None
        db.session.commit()
    required = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": "ABCD-EFGH"})
    assert required.status_code == 409
    assert required.get_json()["error"] == "confirmation_required"
    code = post(recipient, f"/api/v1/family/invitations/{invitation_id}/confirmation").get_json()["confirmation"]["code"]
    assert post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": code}).status_code == 200


def test_upgrade_adds_confirmation_columns_without_touching_invitations(app):
    with app.app_context():
        db.create_all()
        db.session.execute(text("DROP INDEX uq_family_invitations_confirm_token"))
        for column in ("confirm_token_hash", "confirm_code_hash", "confirm_failures"):
            db.session.execute(text(f"ALTER TABLE family_invitations DROP COLUMN {column}"))
        db.session.commit()
    owner, recipient = app.test_client(), app.test_client()
    register(owner, "招待する人")
    register(recipient, "申請する人")
    with app.app_context():
        token_hash = hashlib.sha256(b"legacy").digest()
        db.session.execute(text(
            "INSERT INTO family_invitations (id, created_at, sender_user_id, kind, token_hash, status, expires_at) "
            "SELECT 'legacy-invitation', CURRENT_TIMESTAMP, id, 'open', :hash, 'pending', '2099-01-01 00:00:00' "
            "FROM users LIMIT 1"
        ), {"hash": token_hash})
        db.session.commit()
        assert "20261029_family_invite_confirmation" in apply_schema_upgrades()
        _add_invitation_confirmation()
        names = {column["name"] for column in inspect(db.engine).get_columns("family_invitations")}
        assert {"confirm_token_hash", "confirm_code_hash", "confirm_failures"} <= names
        indexes = {index["name"]: index for index in inspect(db.engine).get_indexes("family_invitations")}
        assert indexes["uq_family_invitations_confirm_token"]["unique"]
        row = db.session.get(FamilyInvitation, "legacy-invitation")
        assert row.status == "pending" and row.confirm_failures == 0 and row.token_hash == token_hash
