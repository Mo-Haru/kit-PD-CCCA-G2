"""期限切れの家族共有は、内容を更新できない（受信者に届かない版を作らない）。"""

from datetime import timedelta

from app.extensions import db
from app.models import FamilyShare, UserDataKey, utcnow
from app.services import family_service
from tests.test_protection import (
    b64encode, connect_pair, hpke_keypair, hpke_seal, opaque_copy, post, register, share_payload,
)


def expired_share(app, client):
    with app.app_context():
        db.create_all()
    _passkey, owner, _public = register(client, "共有する人")
    public, _ = hpke_keypair()
    recipient, _p, recipient_body, connection_id = connect_pair(app, client, None, "受け取る人", public)
    created, share_id, _aad = share_payload(client, connection_id, recipient_body["user_id"],
                                            recipient_body["encryption_key_id"], public, b'{"schema":1,"items":{}}')
    assert created.status_code == 200
    with app.app_context():
        row = db.session.get(FamilyShare, share_id)
        row.created_at = utcnow() - timedelta(days=2)
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
        owner_copy = opaque_copy()
        owner_copy["data_key_id"] = db.session.query(UserDataKey).filter_by(user_id=owner["user_id"]).one().id
    aad = f"hpke-v1|family|{share_id}|ignored|{recipient_body['user_id']}|{recipient_body['encryption_key_id']}|2".encode()
    enc, ciphertext = hpke_seal(public, b'{"schema":1,"items":{}}', aad)
    body = {"expected_version": 1, "recipient_key_id": recipient_body["encryption_key_id"], "confirmed": True,
            "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)}, "owner_copy": owner_copy}
    return recipient, share_id, body


def version_of(app, share_id):
    with app.app_context():
        return db.session.get(FamilyShare, share_id).current_version


def test_updating_an_expired_share_is_refused(app, client):
    recipient, share_id, body = expired_share(app, client)
    refused = post(client, f"/api/v1/family/shares/{share_id}/update", body)
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "share_expired"
    assert version_of(app, share_id) == 1
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404


def test_a_share_expiring_after_the_check_is_still_refused(app, client, monkeypatch):
    """状態を確かめた時点では期限内でも、更新の時点で切れていれば条件付き更新で断る。"""
    recipient, share_id, body = expired_share(app, client)
    checks = []
    original = family_service._expired

    def first_check_passes(share):
        checks.append(share.id)
        return False if len(checks) == 1 else original(share)

    monkeypatch.setattr(family_service, "_expired", first_check_passes)
    refused = post(client, f"/api/v1/family/shares/{share_id}/update", body)
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "share_expired"
    assert version_of(app, share_id) == 1


def test_extending_the_expiry_makes_the_share_updatable_again(app, client):
    recipient, share_id, body = expired_share(app, client)
    later = (utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert post(client, f"/api/v1/family/shares/{share_id}/expiry", {"expires_at": later}).status_code == 200
    updated = post(client, f"/api/v1/family/shares/{share_id}/update", body)
    assert updated.status_code == 200, updated.get_json()
    assert version_of(app, share_id) == 2
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 200
