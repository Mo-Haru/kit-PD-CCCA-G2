"""パスキー、家族共有、QR閲覧、端末移行の権限と暗号境界。"""

from __future__ import annotations

import hashlib
import json
import secrets
import subprocess
import threading
from datetime import timedelta, timezone
from pathlib import Path

import pytest
from pyhpke import AEADId, CipherSuite, KDFId, KEMId
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    FamilyConnection,
    FamilyShare,
    PasskeyCredential,
    PrivateAnswerDocument,
    QrShare,
    QrViewSession,
    User,
    UserEncryptionKey,
    VerifiedContact,
    WebAuthnChallenge,
    utcnow,
)
from app.schema_upgrade import apply_schema_upgrades, set_sqlite_foreign_keys
from app.services.support import b64decode, b64encode
from config import TestingConfig
from tests.soft_passkey import SoftPasskey

ORIGIN = "http://localhost"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client(app):
    with app.app_context():
        db.create_all()
    return app.test_client()


def post(client, path, body=None, headers=None):
    extra = dict(headers or {})
    cookie = client.get_cookie("wn_csrf")
    if cookie is not None:
        extra.setdefault("X-CSRF-Token", cookie.value)
    return client.post(path, json=body if body is not None else {}, headers=extra)


def put(client, path, body):
    extra = {}
    cookie = client.get_cookie("wn_csrf")
    if cookie is not None:
        extra["X-CSRF-Token"] = cookie.value
    return client.put(path, json=body, headers=extra)


def envelope(prf_input):
    return {
        "prf_input": prf_input,
        "kdf_salt": b64encode(secrets.token_bytes(32)),
        "wrapped_key": b64encode(secrets.token_bytes(48)),
        "nonce": b64encode(secrets.token_bytes(12)),
        "kdf_version": 1,
        "crypto_version": 1,
    }


def bundle():
    return {
        "ciphertext": b64encode(secrets.token_bytes(32)),
        "nonce": b64encode(secrets.token_bytes(12)),
        "crypto_version": 1,
    }


def suite():
    return CipherSuite.new(KEMId.DHKEM_X25519_HKDF_SHA256, KDFId.HKDF_SHA256, AEADId.AES128_GCM)


def hpke_keypair():
    pair = suite().kem.derive_key_pair(secrets.token_bytes(32))
    return pair.public_key.to_public_bytes(), pair.private_key.to_private_bytes()


def hpke_seal(public_key, plaintext, aad):
    hpke = suite()
    enc, context = hpke.create_sender_context(hpke.kem.deserialize_public_key(public_key), info=aad)
    return enc, context.seal(plaintext, aad)


def hpke_open(private_key, enc, ciphertext, aad):
    hpke = suite()
    context = hpke.create_recipient_context(enc, hpke.kem.deserialize_private_key(private_key), info=aad)
    return context.open(ciphertext, aad)


def stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def register(client, name="利用者", public_key=None, synced=True):
    if public_key is None:
        public_key, _private = hpke_keypair()
    options = post(client, "/api/v1/auth/register/options", {"display_name": name})
    assert options.status_code == 200, options.get_json()
    payload = options.get_json()
    passkey = SoftPasskey(synced=synced)
    created = post(client, "/api/v1/auth/register/verify", {
        "credential": passkey.register(payload["public_key"], ORIGIN),
        "envelope": envelope(payload["prf_input"]),
        "encryption_public_key": b64encode(public_key),
        "key_bundle": bundle(),
    })
    assert created.status_code == 200, created.get_json()
    return passkey, created.get_json(), public_key


def login(client, passkey, origin=ORIGIN, sign_count=None):
    options = post(client, "/api/v1/auth/login/options", {})
    assert options.status_code == 200, options.get_json()
    credential = passkey.authenticate(options.get_json()["public_key"], origin, sign_count=sign_count)
    return post(client, "/api/v1/auth/login/verify", {"credential": credential}), credential


def unlock(client, passkey):
    options = post(client, "/api/v1/auth/unlock/options")
    assert options.status_code == 200, options.get_json()
    credential = passkey.authenticate(options.get_json()["public_key"], ORIGIN)
    response = post(client, "/api/v1/auth/unlock/verify", {"credential": credential})
    assert response.status_code == 200, response.get_json()


def opaque_copy():
    return {
        "data_key_id": None,
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(secrets.token_bytes(32)),
        "crypto_version": 1,
    }


def test_passkey_registration_login_and_sign_count_zero(client, app):
    marker = "PLAINTEXT-KEY"
    passkey, created, _public = register(client)
    assert "storage_key" not in created
    assert marker not in client.get("/api/v1/auth/session").get_data(as_text=True)
    session = client.get("/api/v1/auth/session").get_json()
    assert session["authenticated"] is True
    assert session["reauthenticated"] is True
    logged_out = post(client, "/api/v1/auth/logout")
    assert logged_out.status_code == 200
    assert client.get("/api/v1/auth/session").get_json()["authenticated"] is False
    first, _credential = login(client, passkey)
    assert first.status_code == 200, first.get_json()
    assert first.get_json()["unlock_required"] is True
    post(client, "/api/v1/auth/logout")
    second, _credential = login(client, passkey)
    assert second.status_code == 200, second.get_json()
    with app.app_context():
        stored = db.session.query(PasskeyCredential).one()
        assert stored.sign_count == 0
        assert stored.revoked_at is None


def test_webauthn_rejects_bad_origin_replay_expiry_and_handle(client, app):
    passkey, _created, _public = register(client)
    post(client, "/api/v1/auth/logout")
    wrong, _credential = login(client, passkey, origin="https://evil.example")
    assert wrong.status_code == 400
    assert wrong.get_json()["error"] == "webauthn_invalid"
    options = post(client, "/api/v1/auth/login/options", {})
    credential = passkey.authenticate(options.get_json()["public_key"], ORIGIN)
    assert post(client, "/api/v1/auth/login/verify", {"credential": credential}).status_code == 200
    again = post(client, "/api/v1/auth/login/verify", {"credential": credential})
    assert again.status_code == 400
    assert again.get_json()["error"] == "challenge_invalid"
    post(client, "/api/v1/auth/logout")
    options = post(client, "/api/v1/auth/login/options", {})
    with app.app_context():
        row = db.session.query(WebAuthnChallenge).filter_by(consumed_at=None).one()
        row.created_at = utcnow() - timedelta(minutes=10)
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    expired = passkey.authenticate(options.get_json()["public_key"], ORIGIN)
    assert post(client, "/api/v1/auth/login/verify", {"credential": expired}).status_code == 400
    options = post(client, "/api/v1/auth/login/options", {})
    mismatched = passkey.authenticate(options.get_json()["public_key"], ORIGIN)
    mismatched["response"]["userHandle"] = b64encode(secrets.token_bytes(32))
    rejected = post(client, "/api/v1/auth/login/verify", {"credential": mismatched})
    assert rejected.status_code == 400
    assert client.get("/api/v1/auth/session").get_json()["authenticated"] is False


def test_bad_signature_does_not_revoke_existing_passkey(client, app):
    passkey, _created, _public = register(client)
    options = post(client, "/api/v1/auth/passkeys/options")
    assert options.status_code == 200, options.get_json()
    second = SoftPasskey()
    added = post(client, "/api/v1/auth/passkeys/verify", {
        "credential": second.register(options.get_json()["public_key"], ORIGIN),
        "envelope": envelope(options.get_json()["prf_input"]),
    })
    assert added.status_code == 200, added.get_json()
    post(client, "/api/v1/auth/logout")
    options = post(client, "/api/v1/auth/login/options", {})
    credential = second.authenticate(options.get_json()["public_key"], ORIGIN)
    raw = bytearray(b64decode(credential["response"]["signature"]))
    raw[0] ^= 0x01
    credential["response"]["signature"] = b64encode(bytes(raw))
    failed = post(client, "/api/v1/auth/login/verify", {"credential": credential})
    assert failed.status_code == 400
    with app.app_context():
        assert db.session.query(PasskeyCredential).filter(PasskeyCredential.revoked_at.is_(None)).count() == 2
    success, _credential = login(client, passkey)
    assert success.status_code == 200


def test_last_passkey_requires_explicit_confirmation(client):
    passkey, _created, _public = register(client)
    listed = client.get("/api/v1/auth/passkeys").get_json()["passkeys"]
    passkey_id = listed[0]["id"]
    refused = post(client, f"/api/v1/auth/passkeys/{passkey_id}/disable", {})
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "last_passkey"
    confirmed = post(client, f"/api/v1/auth/passkeys/{passkey_id}/disable", {"confirm_last": True})
    assert confirmed.status_code == 200
    failed, _credential = login(client, passkey)
    assert failed.status_code == 400


def test_csrf_and_rate_limit_create_nothing(app):
    with app.app_context():
        db.create_all()
    browser = app.test_client()
    options = browser.post("/api/v1/auth/register/options", json={"display_name": "仮"})
    assert options.status_code == 200
    missing = browser.post("/api/v1/auth/register/verify", json={"credential": {}})
    assert missing.status_code == 403
    assert missing.get_json()["error"] == "csrf"
    with app.app_context():
        assert db.session.query(User).count() == 0

    class Tight(TestingConfig):
        RATE_LIMITS = {**TestingConfig.RATE_LIMITS, "auth_options": (2, 600)}

    from app import create_app
    limited = create_app(Tight)
    with limited.app_context():
        db.create_all()
    tight = limited.test_client()
    assert post(tight, "/api/v1/auth/login/options", {}).status_code == 200
    assert post(tight, "/api/v1/auth/login/options", {}).status_code == 200
    blocked = post(tight, "/api/v1/auth/login/options", {})
    assert blocked.status_code == 429
    assert blocked.get_json()["error"] == "rate_limited"
    assert "wn_session" not in blocked.headers.getlist("Set-Cookie")
    with limited.app_context():
        assert db.session.query(User).count() == 0


def test_sensitive_pages_keep_other_pages_unstyled_by_csp(client):
    auth = client.get("/auth")
    assert auth.status_code == 200
    assert "パスキーをすべて失う" in auth.get_data(as_text=True)
    assert "<fieldset disabled>" not in auth.get_data(as_text=True)
    assert auth.headers["X-Content-Type-Options"] == "nosniff"
    assert auth.headers["Referrer-Policy"] == "no-referrer"
    assert "no-store" in auth.headers["Cache-Control"]
    assert "script-src 'self'" in auth.headers["Content-Security-Policy"]
    assert "unsafe-inline" not in auth.headers["Content-Security-Policy"]
    answers = client.get("/answers")
    assert "未実装" in answers.get_data(as_text=True)
    assert "Content-Security-Policy" not in answers.headers
    assert answers.headers["X-Content-Type-Options"] == "nosniff"
    family = client.get("/family")
    transfer = client.get("/transfer")
    assert family.status_code == transfer.status_code == 200


def test_answers_are_private_conflict_safe_and_reject_plaintext(client, app):
    _passkey, owner, _public = register(client, "所有者")
    other = app.test_client()
    register(other, "別の人")
    saved = put(client, "/api/v1/answers/current", {
        "expected_revision": 0,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(b"ciphertext-v1" + secrets.token_bytes(16)),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert saved.status_code == 200, saved.get_json()
    leaked = put(client, "/api/v1/answers/current", {
        "expected_revision": 1,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(secrets.token_bytes(24)),
        "crypto_version": 1,
        "schema_version": 1,
        "plaintext": "SECRET_MARKER",
        "owner_user_id": other.get("/api/v1/auth/session").get_json()["user_id"],
    })
    assert leaked.status_code == 400
    assert "SECRET_MARKER" not in leaked.get_data(as_text=True)
    second = put(client, "/api/v1/answers/current", {
        "expected_revision": 1,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(b"ciphertext-v2" + secrets.token_bytes(16)),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert second.status_code == 200
    stale = put(client, "/api/v1/answers/current", {
        "expected_revision": 1,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(secrets.token_bytes(24)),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert stale.status_code == 409
    own = client.get("/api/v1/answers/current").get_json()
    theirs = other.get("/api/v1/answers/current").get_json()
    assert own["revision"] == 2
    assert theirs["document"] is None
    assert own["document"]["ciphertext"] not in other.get("/api/v1/answers/current").get_data(as_text=True)
    with app.app_context():
        assert db.session.query(PrivateAnswerDocument).count() == 2
        for row in db.session.query(AuditLog).all():
            assert "SECRET_MARKER" not in (row.error_code or "")


def connect_pair(app, owner_client, owner_passkey, recipient_name, recipient_public):
    recipient = app.test_client()
    recipient_passkey, recipient_body, _public = register(
        recipient, recipient_name, public_key=recipient_public,
    )
    invited = post(owner_client, "/api/v1/family/invitations", {"kind": "open"})
    assert invited.status_code == 200, invited.get_json()
    token = invited.get_json()["token"]
    assert post(recipient, "/api/v1/family/invitations/request", {"token": token}).status_code == 200
    invitation_id = invited.get_json()["invitation"]["id"]
    confirmed = post(owner_client, f"/api/v1/family/invitations/{invitation_id}/confirm")
    assert confirmed.status_code == 200, confirmed.get_json()
    return recipient, recipient_passkey, recipient_body, confirmed.get_json()["connection_id"]


def test_family_invitation_is_single_use_and_confirmed(client, app):
    owner_passkey, owner, _public = register(client, "招待する人")
    page = client.get("/family/invite")
    assert page.status_code == 200
    with app.app_context():
        assert db.session.get(User, owner["user_id"]) is not None
    bob_public, _bob_private = hpke_keypair()
    bob, _bob_passkey, bob_body, connection_id = connect_pair(app, client, owner_passkey, "受け取る人", bob_public)
    outsider = app.test_client()
    register(outsider, "第三者")
    invited = client.get("/api/v1/family/invitations").get_json()["invitations"][0]
    reused = post(outsider, "/api/v1/family/invitations/request", {"token": "already-used"})
    assert reused.status_code == 400
    assert connection_id
    assert bob_body["user_id"] != owner["user_id"]
    from app.contact_hash import HASH_VERSION, address_hmac
    unknown_email = "missing@example.test"
    email = "family@example.test"
    with app.app_context():
        db.session.add(VerifiedContact(
            user_id=bob_body["user_id"], kind="email",
            address_hash=address_hmac(email), address_hash_version=HASH_VERSION,
        ))
        db.session.commit()
        connections_before = db.session.query(FamilyConnection).count()
    unknown = post(client, "/api/v1/family/invitations", {"kind": "email", "email": unknown_email})
    emailed = post(client, "/api/v1/family/invitations", {"kind": "email", "email": "Family@Example.test"})
    assert unknown.status_code == emailed.status_code == 200
    unknown_body = unknown.get_json()
    emailed_body = emailed.get_json()
    assert set(unknown_body) == set(emailed_body) == {"invitation", "token", "invite_path"}
    assert set(unknown_body["invitation"]) == set(emailed_body["invitation"])
    assert unknown_body["invitation"]["kind"] == emailed_body["invitation"]["kind"] == "email"
    assert unknown_body["invitation"]["status"] == emailed_body["invitation"]["status"] == "pending"
    assert unknown_body["invitation"]["recipient_display_name"] == emailed_body["invitation"]["recipient_display_name"]
    listed = client.get("/api/v1/family/invitations").get_json()["invitations"]
    email_rows = [row for row in listed if row["kind"] == "email"]
    assert len(email_rows) == 2
    assert {row["status"] for row in email_rows} == {"pending"}
    assert {row["recipient_display_name"] for row in email_rows} == {None}
    combined = unknown.get_data(as_text=True) + emailed.get_data(as_text=True) + client.get("/api/v1/family/invitations").get_data(as_text=True)
    assert unknown_email not in combined and email not in combined and "Family@Example.test" not in combined
    denied = post(outsider, "/api/v1/family/invitations/accept", {"token": emailed_body["token"]})
    assert denied.status_code == 403
    wrong = post(bob, "/api/v1/family/invitations/accept", {"token": unknown_body["token"]})
    assert wrong.status_code == 403
    assert wrong.get_json()["error"] == denied.get_json()["error"] == "recipient_mismatch"
    accepted = post(bob, "/api/v1/family/invitations/accept", {"token": emailed_body["token"]})
    assert accepted.status_code == 409
    assert accepted.get_json()["error"] == "already_connected"
    with app.app_context():
        assert db.session.query(FamilyConnection).count() == connections_before
        stored = db.session.query(VerifiedContact).filter_by(user_id=bob_body["user_id"]).one().address_hash
        assert stored != hashlib.sha256(email.encode()).digest()
    assert invited["status"] in {"accepted", "pending", "awaiting_sender"}


def share_payload(client, connection_id, recipient_id, key_id, public_key, plaintext, share_id=None):
    share_id = share_id or str(__import__("uuid").uuid4())
    aad = f"hpke-v1|family|{share_id}|ignored|{recipient_id}|{key_id}|1".encode()
    enc, ciphertext = hpke_seal(public_key, plaintext, aad)
    session = client.get("/api/v1/auth/session").get_json()
    body = {
        "id": share_id,
        "connection_id": connection_id,
        "recipient_key_id": key_id,
        "expires_at": None,
        "confirmed": True,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
        "owner_copy": opaque_copy(),
    }
    with client.application.app_context():
        from app.models import UserDataKey
        data_key = db.session.query(UserDataKey).filter_by(user_id=session["user_id"], retired_at=None).one()
        body["owner_copy"]["data_key_id"] = data_key.id
    return post(client, "/api/v1/family/shares", body), share_id, aad


def test_family_share_is_explicit_per_recipient_and_stops(client, app):
    _owner_passkey, owner, _owner_public = register(client, "本人")
    alice_public, alice_private = hpke_keypair()
    bob_public, bob_private = hpke_keypair()
    alice, _alice_passkey, alice_body, alice_connection = connect_pair(app, client, None, "家族A", alice_public)
    bob, _bob_passkey, bob_body, bob_connection = connect_pair(app, client, None, "家族B", bob_public)
    selected = json.dumps({"schema": 1, "items": {"q1": {"state": "answered", "value": "dummy"}}}).encode()
    hidden = json.dumps({"schema": 1, "items": {"q1": {"state": "answered"}, "q2": {"state": "declined"}}}).encode()
    first, alice_share, alice_aad = share_payload(
        client, alice_connection, alice_body["user_id"], alice_body["encryption_key_id"], alice_public, selected,
    )
    assert first.status_code == 200, first.get_json()
    second, bob_share, bob_aad = share_payload(
        client, bob_connection, bob_body["user_id"], bob_body["encryption_key_id"], bob_public, hidden,
    )
    assert second.status_code == 200, second.get_json()
    alice_view = alice.get(f"/api/v1/family/received/{alice_share}")
    bob_view = bob.get(f"/api/v1/family/received/{bob_share}")
    assert alice_view.status_code == bob_view.status_code == 200
    alice_opened = hpke_open(
        alice_private, b64decode(alice_view.get_json()["share"]["current"]["hpke_enc"]),
        b64decode(alice_view.get_json()["share"]["current"]["ciphertext"]), alice_aad,
    )
    assert b"q2" not in alice_opened
    with pytest.raises(Exception):
        hpke_open(
            bob_private, b64decode(alice_view.get_json()["share"]["current"]["hpke_enc"]),
            b64decode(alice_view.get_json()["share"]["current"]["ciphertext"]), alice_aad,
        )
    assert bob.get(f"/api/v1/family/received/{alice_share}").status_code == 404
    assert bob.get(f"/api/v1/family/shares/{alice_share}/history").status_code == 404
    saved = put(client, "/api/v1/answers/current", {
        "expected_revision": 0,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(secrets.token_bytes(24)),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert saved.status_code == 200
    with app.app_context():
        assert db.session.get(FamilyShare, alice_share).current_version == 1
        from app.models import UserDataKey
        owner_copy = opaque_copy()
        owner_copy["data_key_id"] = db.session.query(UserDataKey).filter_by(user_id=owner["user_id"]).one().id
        current = 1
    replacement = json.dumps({"schema": 1, "items": {"q1": {"state": "undecided"}}}).encode()
    aad = f"hpke-v1|family|{alice_share}|ignored|{alice_body['user_id']}|{alice_body['encryption_key_id']}|2".encode()
    enc, ciphertext = hpke_seal(alice_public, replacement, aad)
    updated = post(client, f"/api/v1/family/shares/{alice_share}/update", {
        "expected_version": current,
        "recipient_key_id": alice_body["encryption_key_id"],
        "confirmed": True,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
        "owner_copy": owner_copy,
    })
    assert updated.status_code == 200, updated.get_json()
    latest = alice.get(f"/api/v1/family/received/{alice_share}").get_json()["share"]
    assert latest["current_version"] == current + 1
    assert b64encode(ciphertext) == latest["current"]["ciphertext"]
    history = client.get(f"/api/v1/family/shares/{alice_share}/history")
    assert history.status_code == 200
    assert len(history.get_json()["history"]) == current + 1
    stopped = post(client, f"/api/v1/family/shares/{bob_share}/stop")
    assert stopped.status_code == 200
    assert bob.get(f"/api/v1/family/received/{bob_share}").status_code == 404
    assert client.get(f"/api/v1/family/shares/{bob_share}/history").status_code == 200
    assert post(client, f"/api/v1/family/connections/{alice_connection}/disconnect").status_code == 200
    assert alice.get(f"/api/v1/family/received/{alice_share}").status_code == 404


def test_recipient_key_rotation_blocks_stale_update(client, app):
    register(client, "本人")
    public, _private = hpke_keypair()
    recipient, _passkey, body, connection_id = connect_pair(app, client, None, "家族", public)
    created, share_id, _aad = share_payload(
        client, connection_id, body["user_id"], body["encryption_key_id"], public, b"version-one",
    )
    assert created.status_code == 200, created.get_json()
    unlock(recipient, _passkey)
    new_public, _new_private = hpke_keypair()
    changed = post(recipient, "/api/v1/encryption-keys", {
        "public_key": b64encode(new_public),
        "replaces": body["encryption_key_id"],
        "acknowledge_key_change": True,
    })
    assert changed.status_code == 200, changed.get_json()
    with app.app_context():
        share = db.session.get(FamilyShare, share_id)
        assert share.status == "key_pending"
        version_before = share.current_version
    stale = post(client, f"/api/v1/family/shares/{share_id}/update", {
        "expected_version": version_before,
        "recipient_key_id": body["encryption_key_id"],
        "confirmed": True,
        "version": {"hpke_enc": b64encode(secrets.token_bytes(32)), "ciphertext": b64encode(secrets.token_bytes(32))},
        "owner_copy": {
            "data_key_id": created.get_json()["share"]["owner_copy"]["data_key_id"],
            "nonce": b64encode(secrets.token_bytes(12)),
            "ciphertext": b64encode(secrets.token_bytes(32)),
            "crypto_version": 1,
        },
    })
    assert stale.status_code == 409
    assert stale.get_json()["error"] == "key_changed"
    assert stale.get_json()["current_key_id"] == changed.get_json()["encryption_key_id"]
    with app.app_context():
        assert db.session.get(FamilyShare, share_id).current_version == version_before


def qr_body(owner_id, mode, public_key, token, plaintext, expires=None):
    share_id = secrets.token_urlsafe(32)
    aad = f"hpke-v1|qr|{share_id}|{owner_id}|qr|{b64encode(public_key)}|1".encode()
    enc, ciphertext = hpke_seal(public_key, plaintext, aad)
    return {
        "id": share_id,
        "mode": mode,
        "expires_at": expires,
        "public_key": b64encode(public_key),
        "start_token_hash": b64encode(hashlib.sha256(token).digest()),
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
    }, aad


def test_qr_view_session_is_separate_from_account(client, app):
    _passkey, owner, _public = register(client, "QRの人")
    public, private = hpke_keypair()
    token = secrets.token_bytes(32)
    body, aad = qr_body(owner["user_id"], "emergency", public, token, b"qr-secret")
    created = post(client, "/api/v1/qr-shares", body)
    assert created.status_code == 200, created.get_json()
    share_id = body["id"]
    preview = client.get(f"/view/{share_id}")
    assert preview.status_code == 200
    assert b64encode(token) not in preview.get_data(as_text=True)
    assert "qr-secret" not in preview.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(QrShare, share_id).consumed_at is None
    viewer = app.test_client()
    attempt = b64encode(secrets.token_bytes(32))
    denied = post(viewer, f"/api/v1/qr-shares/{share_id}/start", {"start_token": b64encode(secrets.token_bytes(32)), "attempt_id": attempt})
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "qr_denied"
    started = post(viewer, f"/api/v1/qr-shares/{share_id}/start", {"start_token": b64encode(token), "attempt_id": attempt})
    assert started.status_code == 200, started.get_json()
    session_id = started.get_json()["session_id"]
    absolute = started.get_json()["absolute_expires_at"]
    current = viewer.get("/api/v1/qr-view/current")
    assert current.status_code == 200, current.get_json()
    opened = hpke_open(
        private, b64decode(current.get_json()["version"]["hpke_enc"]),
        b64decode(current.get_json()["version"]["ciphertext"]), aad,
    )
    assert opened == b"qr-secret"
    assert viewer.get("/api/v1/qr-view/current").get_json()["session"]["absolute_expires_at"] == absolute
    assert viewer.get("/api/v1/answers/current").status_code == 401
    retry = post(viewer, f"/api/v1/qr-shares/{share_id}/start", {"start_token": b64encode(token), "attempt_id": attempt})
    assert retry.status_code == 200
    assert retry.get_json()["session_id"] == session_id
    other_attempt = post(app.test_client(), f"/api/v1/qr-shares/{share_id}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert other_attempt.status_code == 409
    with app.app_context():
        consumed = db.session.get(QrShare, share_id).consumed_at
    enc, ciphertext = hpke_seal(public, b"qr-secret-2", aad.replace(b"|1", b"|2"))
    updated = post(client, f"/api/v1/qr-shares/{share_id}/update", {
        "expected_version": 1,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
    })
    assert updated.status_code == 200, updated.get_json()
    with app.app_context():
        assert db.session.get(QrShare, share_id).consumed_at == consumed
    refreshed = viewer.get("/api/v1/qr-view/current")
    assert hpke_open(
        private, b64decode(refreshed.get_json()["version"]["hpke_enc"]),
        b64decode(refreshed.get_json()["version"]["ciphertext"]), aad.replace(b"|1", b"|2"),
    ) == b"qr-secret-2"
    assert post(client, f"/api/v1/qr-shares/{share_id}/stop").status_code == 200
    assert viewer.get("/api/v1/qr-view/current").status_code == 403


def test_timed_qr_allows_two_sessions_until_expiry(client, app):
    _passkey, owner, _public = register(client)
    public, _private = hpke_keypair()
    token = secrets.token_bytes(32)
    expires = stamp(utcnow() + timedelta(hours=1))
    body, _aad = qr_body(owner["user_id"], "timed", public, token, b"timed", expires)
    assert post(client, "/api/v1/qr-shares", body).status_code == 200
    first_client = app.test_client()
    first = post(first_client, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    second = post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert first.status_code == second.status_code == 200
    assert first.get_json()["session_id"] != second.get_json()["session_id"]
    absolute = first.get_json()["absolute_expires_at"]
    with app.app_context():
        row = db.session.get(QrViewSession, first.get_json()["session_id"])
        row.issued_at = utcnow() - timedelta(minutes=10)
        row.idle_expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert first_client.get("/api/v1/qr-view/current").status_code == 401
    with app.app_context():
        assert stamp(db.session.get(QrViewSession, first.get_json()["session_id"]).absolute_expires_at) == absolute
        share = db.session.get(QrShare, body["id"])
        share.created_at = utcnow() - timedelta(hours=2)
        share.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    expired = post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert expired.status_code == 403


def test_emergency_qr_parallel_attempts(tmp_path):
    from app import create_app
    database = tmp_path / "qr.db"

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + database.as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 5}}

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
    owner = application.test_client()
    _passkey, created, _public = register(owner)
    public, _private = hpke_keypair()
    token = secrets.token_bytes(32)
    body, _aad = qr_body(created["user_id"], "emergency", public, token, b"once")
    assert post(owner, "/api/v1/qr-shares", body).status_code == 200
    barrier = threading.Barrier(2)
    results = []

    def attempt(value):
        barrier.wait()
        response = post(application.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
            "start_token": b64encode(token), "attempt_id": b64encode(value),
        })
        results.append(response.status_code)

    threads = [threading.Thread(target=attempt, args=(secrets.token_bytes(32),)) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == [200, 409]
    with application.app_context():
        assert db.session.query(QrViewSession).count() == 1
        assert db.session.get(QrShare, body["id"]).consumed_at is not None


def test_migration_keeps_source_until_target_confirms(client, app):
    source_passkey, _owner, _public = register(client, "移行する人")
    started = post(client, "/api/v1/transfer/start")
    assert started.status_code == 200, started.get_json()
    migration_id = started.get_json()["migration"]["id"]
    target = app.test_client()
    public, private = hpke_keypair()
    bound = post(target, "/api/v1/transfer/bind", {
        "migration_id": migration_id,
        "target_public_key": b64encode(public),
    })
    assert bound.status_code == 200, bound.get_json()
    token = bound.get_json()["transfer_token"]
    swapped = post(app.test_client(), "/api/v1/transfer/bind", {
        "migration_id": migration_id,
        "target_public_key": b64encode(secrets.token_bytes(32)),
    })
    assert swapped.status_code == 409
    status = client.get(f"/api/v1/transfer/{migration_id}").get_json()["migration"]
    assert status["fingerprint"] == bound.get_json()["migration"]["fingerprint"]
    wrong = post(client, f"/api/v1/transfer/{migration_id}/approve", {
        "confirmation_code": "WRONG",
        "fingerprint": status["fingerprint"],
    })
    assert wrong.status_code == 403
    approved = post(client, f"/api/v1/transfer/{migration_id}/approve", {
        "confirmation_code": bound.get_json()["migration"]["confirmation_code"],
        "fingerprint": status["fingerprint"],
    })
    assert approved.status_code == 200, approved.get_json()
    aad = f"hpke-v1|migration|{migration_id}|owner|target|{b64encode(public)}|1".encode()
    enc, ciphertext = hpke_seal(public, b"bundle-secret", aad)
    outsider = app.test_client()
    register(outsider, "他人")
    assert post(outsider, f"/api/v1/transfer/{migration_id}/bundle", {
        "hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext),
    }).status_code == 404
    uploaded = post(client, f"/api/v1/transfer/{migration_id}/bundle", {
        "hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext),
    })
    assert uploaded.status_code == 200, uploaded.get_json()
    downloaded = target.get("/api/v1/transfer/target/bundle", headers={"X-Transfer-Token": token})
    assert downloaded.status_code == 200
    assert hpke_open(private, b64decode(downloaded.get_json()["hpke_enc"]), b64decode(downloaded.get_json()["ciphertext"]), aad) == b"bundle-secret"
    stranger = app.test_client()
    stranger.get("/transfer")
    premature = post(stranger, f"/api/v1/transfer/{migration_id}/finalize", {"confirmed": True})
    assert premature.status_code == 401
    still_source, _credential = login(app.test_client(), source_passkey)
    assert still_source.status_code == 200
    options = post(target, "/api/v1/transfer/passkey/options", headers={"X-Transfer-Token": token})
    assert options.status_code == 200, options.get_json()
    target_passkey = SoftPasskey()
    verified = post(target, "/api/v1/transfer/passkey/verify", {
        "credential": target_passkey.register(options.get_json()["public_key"], ORIGIN),
        "envelope": envelope(options.get_json()["prf_input"]),
    }, headers={"X-Transfer-Token": token})
    assert verified.status_code == 200, verified.get_json()
    logged, _credential = login(target, target_passkey)
    assert logged.status_code == 200, logged.get_json()
    login_only = post(target, f"/api/v1/transfer/{migration_id}/finalize", {"confirmed": True})
    assert login_only.status_code == 403
    assert login_only.get_json()["error"] == "reauth_required"
    unlock(target, target_passkey)
    unconfirmed = post(target, f"/api/v1/transfer/{migration_id}/finalize", {"confirmed": True})
    assert unconfirmed.status_code == 409
    assert unconfirmed.get_json()["error"] == "decrypt_unconfirmed"
    outsider_ack = post(outsider, f"/api/v1/transfer/{migration_id}/decrypt-confirmed", {})
    assert outsider_ack.status_code == 404
    still_source, _credential = login(app.test_client(), source_passkey)
    assert still_source.status_code == 200
    acknowledged = post(target, f"/api/v1/transfer/{migration_id}/decrypt-confirmed", {})
    assert acknowledged.status_code == 200, acknowledged.get_json()
    assert acknowledged.get_json()["decrypt_confirmed"] is True
    done = post(target, f"/api/v1/transfer/{migration_id}/finalize", {"confirmed": True})
    assert done.status_code == 200, done.get_json()
    revoked, _credential = login(app.test_client(), source_passkey)
    assert revoked.status_code == 400
    again, _credential = login(app.test_client(), target_passkey)
    assert again.status_code == 200


def test_cancelled_or_expired_migration_keeps_source_passkey(client, app):
    source_passkey, _owner, _public = register(client)
    started = post(client, "/api/v1/transfer/start").get_json()["migration"]["id"]
    target = app.test_client()
    public, _private = hpke_keypair()
    post(target, "/api/v1/transfer/bind", {"migration_id": started, "target_public_key": b64encode(public)})
    assert post(client, f"/api/v1/transfer/{started}/cancel").status_code == 200
    still, _credential = login(app.test_client(), source_passkey)
    assert still.status_code == 200
    with app.app_context():
        from app.models import DeviceMigration
        row = db.session.get(DeviceMigration, started)
        assert row.status == "cancelled"
        assert db.session.query(PasskeyCredential).filter_by(user_id=row.user_id, revoked_at=None).count() == 1


def test_schema_upgrade_recreates_missing_table_without_deleting_users(app):
    with app.app_context():
        db.create_all()
        apply_schema_upgrades()
        user = User(display_name="残す人")
        db.session.add(user)
        db.session.commit()
        user_id = user.id
        db.session.execute(text("DROP TABLE rate_limit_buckets"))
        db.session.execute(text("DELETE FROM schema_migrations"))
        db.session.commit()
        assert "rate_limit_buckets" not in inspect(db.engine).get_table_names()
        apply_schema_upgrades()
        assert "rate_limit_buckets" in inspect(db.engine).get_table_names()
        assert db.session.get(User, user_id).display_name == "残す人"


def test_stale_answer_revision_keeps_the_first_writer(client, app):
    passkey, owner, _public = register(client, "編集A")
    other = app.test_client()
    logged, _credential = login(other, passkey)
    assert logged.status_code == 200
    first_bytes = b"editor-b-ciphertext"
    second_bytes = b"editor-a-stale-text"
    saved = put(client, "/api/v1/answers/current", {
        "expected_revision": 0,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(first_bytes),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert saved.status_code == 200, saved.get_json()
    missing = put(other, "/api/v1/answers/current", {
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(second_bytes),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert missing.status_code == 400
    assert missing.get_json()["error"] == "invalid_revision"
    stale = put(other, "/api/v1/answers/current", {
        "expected_revision": 0,
        "data_key_id": owner["data_key_id"],
        "nonce": b64encode(secrets.token_bytes(12)),
        "ciphertext": b64encode(second_bytes),
        "crypto_version": 1,
        "schema_version": 1,
    })
    assert stale.status_code == 409
    assert stale.get_json()["error"] == "revision_conflict"
    current = client.get("/api/v1/answers/current").get_json()
    assert current["revision"] == 1
    assert b64decode(current["document"]["ciphertext"]) == first_bytes
    assert second_bytes not in client.get("/api/v1/answers/current").get_data()


def test_family_expiry_disconnect_and_reconnect_keeps_old_share_stopped(client, app):
    register(client, "本人")
    public, private = hpke_keypair()
    recipient, _passkey, body, connection_id = connect_pair(app, client, None, "家族", public)
    plaintext = json.dumps({
        "schema": 1,
        "items": {"q-keep": {"state": "answered", "value": "見せる"}, "q-hide": {"state": "declined"}},
    }, ensure_ascii=False).encode()
    created, share_id, aad = share_payload(
        client, connection_id, body["user_id"], body["encryption_key_id"], public, plaintext,
    )
    assert created.status_code == 200, created.get_json()
    opened = hpke_open(
        private,
        b64decode(created.get_json()["share"]["current"]["hpke_enc"]),
        b64decode(created.get_json()["share"]["current"]["ciphertext"]),
        aad,
    )
    assert "見せる" in opened.decode()
    assert json.loads(opened)["items"]["q-hide"]["state"] == "declined"
    later = stamp(utcnow() + timedelta(days=2))
    changed = post(client, f"/api/v1/family/shares/{share_id}/expiry", {"expires_at": later})
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()["share"]["expires_at"] == later
    seen = recipient.get(f"/api/v1/family/received/{share_id}")
    assert seen.status_code == 200
    assert seen.get_json()["share"]["expires_at"] == later
    assert recipient.get(f"/api/v1/family/shares/{share_id}/history").status_code == 404
    assert post(recipient, f"/api/v1/family/shares/{share_id}/expiry", {"expires_at": later}).status_code == 404
    with app.app_context():
        row = db.session.get(FamilyShare, share_id)
        row.created_at = utcnow() - timedelta(days=2)
        row.expires_at = utcnow() - timedelta(minutes=1)
        version_before = row.current_version
        db.session.commit()
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404
    cleared = post(client, f"/api/v1/family/shares/{share_id}/expiry", {"expires_at": None})
    assert cleared.status_code == 200, cleared.get_json()
    assert cleared.get_json()["share"]["expires_at"] is None
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 200
    with app.app_context():
        assert db.session.get(FamilyShare, share_id).current_version == version_before
    assert post(recipient, f"/api/v1/family/connections/{connection_id}/disconnect").status_code == 200
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404
    assert client.get(f"/api/v1/family/shares/{share_id}/history").status_code == 200
    invited = post(client, "/api/v1/family/invitations", {"kind": "open"})
    assert invited.status_code == 200, invited.get_json()
    assert post(recipient, "/api/v1/family/invitations/request", {"token": invited.get_json()["token"]}).status_code == 200
    confirmed = post(client, f"/api/v1/family/invitations/{invited.get_json()['invitation']['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.get_json()
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404


def _no_secret_material(response, *markers):
    body = response.get_data(as_text=True)
    assert "ciphertext" not in body
    assert "hpke_enc" not in body
    assert "owner_copy" not in body
    for marker in markers:
        assert marker not in body


def test_key_pending_stops_delivery_until_reencrypted(client, app):
    register(client, "本人")
    public, _private = hpke_keypair()
    new_public, new_private = hpke_keypair()
    recipient, _passkey, body, connection_id = connect_pair(app, client, None, "家族", public)
    created, share_id, _aad = share_payload(
        client, connection_id, body["user_id"], body["encryption_key_id"], public, b"version-one",
    )
    assert created.status_code == 200, created.get_json()
    old_ciphertext = created.get_json()["share"]["current"]["ciphertext"]
    before = recipient.get(f"/api/v1/family/received/{share_id}")
    assert before.status_code == 200
    assert before.get_json()["share"]["current"]["ciphertext"] == old_ciphertext
    stopped, stopped_id, _stopped_aad = share_payload(
        client, connection_id, body["user_id"], body["encryption_key_id"], public, b"stopped-share",
    )
    expired, expired_id, _expired_aad = share_payload(
        client, connection_id, body["user_id"], body["encryption_key_id"], public, b"expired-share",
    )
    assert stopped.status_code == expired.status_code == 200
    assert post(client, f"/api/v1/family/shares/{stopped_id}/stop").status_code == 200
    with app.app_context():
        row = db.session.get(FamilyShare, expired_id)
        row.created_at = utcnow() - timedelta(days=2)
        row.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    changed = post(recipient, "/api/v1/encryption-keys", {
        "public_key": b64encode(new_public),
        "replaces": body["encryption_key_id"],
        "acknowledge_key_change": True,
    })
    assert changed.status_code == 200, changed.get_json()
    new_key_id = changed.get_json()["encryption_key_id"]
    with app.app_context():
        assert db.session.get(FamilyShare, share_id).status == "key_pending"
        assert db.session.get(FamilyShare, stopped_id).status == "stopped"
        assert db.session.get(FamilyShare, expired_id).status == "key_pending"
    listed = recipient.get("/api/v1/family/received")
    pending = next(row for row in listed.get_json()["shares"] if row["id"] == share_id)
    assert pending["status"] == "key_pending"
    assert "current" not in pending
    direct = recipient.get(f"/api/v1/family/received/{share_id}")
    assert direct.status_code == 200
    assert direct.get_json()["share"]["status"] == "key_pending"
    assert "current" not in direct.get_json()["share"]
    _no_secret_material(listed, old_ciphertext)
    _no_secret_material(direct, old_ciphertext)
    assert recipient.get(f"/api/v1/family/received/{stopped_id}").status_code == 404
    assert recipient.get(f"/api/v1/family/received/{expired_id}").status_code == 404
    assert stopped_id not in listed.get_data(as_text=True)
    assert expired_id not in listed.get_data(as_text=True)
    source_copy = created.get_json()["share"]["owner_copy"]
    owner_copy = {
        "data_key_id": source_copy["data_key_id"],
        "nonce": source_copy["nonce"],
        "ciphertext": source_copy["ciphertext"],
        "crypto_version": source_copy["crypto_version"],
    }
    aad = f"hpke-v1|family|{share_id}|ignored|{body['user_id']}|{new_key_id}|2".encode()
    enc, ciphertext = hpke_seal(new_public, b"version-two", aad)
    updated = post(client, f"/api/v1/family/shares/{share_id}/update", {
        "expected_version": 1,
        "recipient_key_id": new_key_id,
        "confirmed": True,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
        "owner_copy": owner_copy,
    })
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["share"]["status"] == "active"
    renewed = recipient.get(f"/api/v1/family/received/{share_id}")
    assert renewed.status_code == 200
    assert renewed.get_json()["share"]["current"]["ciphertext"] == b64encode(ciphertext)
    assert renewed.get_json()["share"]["current"]["ciphertext"] != old_ciphertext
    opened = hpke_open(new_private, enc, ciphertext, aad)
    assert opened == b"version-two"
    refused = post(client, f"/api/v1/family/shares/{stopped_id}/update", {
        "expected_version": 1,
        "recipient_key_id": new_key_id,
        "confirmed": True,
        "version": {"hpke_enc": b64encode(enc), "ciphertext": b64encode(ciphertext)},
        "owner_copy": owner_copy,
    })
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "share_stopped"
    assert recipient.get(f"/api/v1/family/received/{expired_id}").status_code == 404
    with app.app_context():
        assert db.session.get(FamilyShare, stopped_id).status == "stopped"
        assert db.session.get(FamilyShare, expired_id).status == "key_pending"
    assert post(recipient, f"/api/v1/family/connections/{connection_id}/disconnect").status_code == 200
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404
    invited = post(client, "/api/v1/family/invitations", {"kind": "open"})
    assert post(recipient, "/api/v1/family/invitations/request", {"token": invited.get_json()["token"]}).status_code == 200
    assert post(client, f"/api/v1/family/invitations/{invited.get_json()['invitation']['id']}/confirm").status_code == 200
    assert recipient.get(f"/api/v1/family/received/{share_id}").status_code == 404
    assert recipient.get(f"/api/v1/family/received/{stopped_id}").status_code == 404
    assert recipient.get(f"/api/v1/family/received/{expired_id}").status_code == 404
    with app.app_context():
        assert db.session.get(FamilyShare, share_id).status == "stopped"
        assert db.session.get(FamilyShare, stopped_id).status == "stopped"
        assert db.session.get(FamilyShare, expired_id).status == "stopped"


def test_email_hash_migration_and_missing_secret(client, app):
    from app.contact_hash import HASH_VERSION, address_hmac
    from app.schema_upgrade import _migrate_contact_hashes, apply_schema_upgrades
    _passkey, owner, _public = register(client, "照合する人")
    normalized = "family@example.test"
    legacy = hashlib.sha256(normalized.encode()).digest()
    with app.app_context():
        user = db.session.get(User, owner["user_id"])
        db.session.add(VerifiedContact(
            user_id=user.id, kind="email", address_hash=legacy, address_hash_version=1,
        ))
        db.session.commit()
        db.session.execute(text("ALTER TABLE verified_contacts RENAME TO verified_contacts_legacy"))
        db.session.execute(text(
            "CREATE TABLE verified_contacts ("
            "id VARCHAR(36) NOT NULL PRIMARY KEY, created_at DATETIME NOT NULL, "
            "user_id VARCHAR(36) NOT NULL, kind VARCHAR(16) NOT NULL, "
            "address_hash BLOB NOT NULL, verified_at DATETIME NOT NULL)"
        ))
        db.session.execute(text(
            "INSERT INTO verified_contacts (id, created_at, user_id, kind, address_hash, verified_at) "
            "SELECT id, created_at, user_id, kind, address_hash, verified_at FROM verified_contacts_legacy"
        ))
        db.session.execute(text("DROP TABLE verified_contacts_legacy"))
        db.session.commit()
        app.config["CONTACT_HASH_KEY"] = None
        with pytest.raises(RuntimeError, match="CONTACT_HASH_KEY"):
            _migrate_contact_hashes()
        unchanged = db.session.execute(text("SELECT address_hash FROM verified_contacts")).scalar()
        assert bytes(unchanged) == legacy
        app.config["CONTACT_HASH_KEY"] = "test-only-contact-hash-key-32b!!"
        _migrate_contact_hashes()
        stored, version = db.session.execute(text(
            "SELECT address_hash, address_hash_version FROM verified_contacts"
        )).one()
        stored = bytes(stored)
        assert stored != legacy
        assert stored == address_hmac("Family@Example.test")
        assert version == HASH_VERSION
        _migrate_contact_hashes()
        assert bytes(db.session.execute(text("SELECT address_hash FROM verified_contacts")).scalar()) == stored
        apply_schema_upgrades()
        apply_schema_upgrades()
        assert bytes(db.session.execute(text("SELECT address_hash FROM verified_contacts")).scalar()) == stored
        app.config["CONTACT_HASH_KEY"] = "another-test-contact-hash-key-32b!"
        assert address_hmac(normalized) != stored
        app.config["CONTACT_HASH_KEY"] = None
    missing = post(client, "/api/v1/family/invitations", {"kind": "email", "email": "nobody@example.test"})
    assert missing.status_code == 503
    assert missing.get_json()["error"] == "contact_hash_unconfigured"
    assert "nobody@example.test" not in missing.get_data(as_text=True)
    with app.app_context():
        leaked = hashlib.sha256(b"nobody@example.test").digest()
        found = db.session.execute(text(
            "SELECT COUNT(*) FROM family_invitations WHERE address_hash = :digest"
        ), {"digest": leaked}).scalar()
        assert found == 0
        app.config["CONTACT_HASH_KEY"] = "test-only-contact-hash-key-32b!!"


def test_emergency_idle_expiry_cannot_be_retried(client, app):
    _passkey, owner, _public = register(client, "緊急QR")
    public, _private = hpke_keypair()
    token = secrets.token_bytes(32)
    body, _aad = qr_body(owner["user_id"], "emergency", public, token, b"once")
    assert post(client, "/api/v1/qr-shares", body).status_code == 200
    viewer = app.test_client()
    attempt = secrets.token_bytes(32)
    page = viewer.get(f"/view/{body['id']}")
    assert page.status_code == 200
    assert "内容を開く" in page.get_data(as_text=True)
    with app.app_context():
        assert db.session.get(QrShare, body["id"]).consumed_at is None
        assert db.session.query(QrViewSession).count() == 0
    started = post(viewer, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(attempt),
    })
    assert started.status_code == 200, started.get_json()
    absolute = started.get_json()["absolute_expires_at"]
    retried = post(viewer, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(attempt),
    })
    assert retried.status_code == 200
    assert retried.get_json()["session_id"] == started.get_json()["session_id"]
    assert retried.get_json()["absolute_expires_at"] == absolute
    with app.app_context():
        row = db.session.get(QrViewSession, started.get_json()["session_id"])
        row.issued_at = utcnow() - timedelta(minutes=10)
        row.idle_expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    expired = post(viewer, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(attempt),
    })
    assert expired.status_code == 401
    assert expired.get_json()["error"] == "qr_session_expired"
    assert viewer.get("/api/v1/qr-view/current").status_code == 401
    with app.app_context():
        row = db.session.get(QrViewSession, started.get_json()["session_id"])
        assert row.idle_expires_at <= utcnow()
        assert stamp(row.absolute_expires_at) == absolute
    fresh = post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert fresh.status_code == 409
    assert fresh.get_json()["error"] == "qr_used"


def test_timed_qr_deadlines_are_not_extended_by_retry(client, app):
    _passkey, owner, _public = register(client)
    public, _private = hpke_keypair()
    token = secrets.token_bytes(32)
    expires = stamp(utcnow() + timedelta(hours=2))
    body, _aad = qr_body(owner["user_id"], "timed", public, token, b"timed", expires)
    assert post(client, "/api/v1/qr-shares", body).status_code == 200
    viewer = app.test_client()
    attempt = secrets.token_bytes(32)
    started = post(viewer, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(attempt),
    })
    assert started.status_code == 200, started.get_json()
    with app.app_context():
        row = db.session.get(QrViewSession, started.get_json()["session_id"])
        row.issued_at = utcnow() - timedelta(minutes=10)
        row.absolute_expires_at = utcnow() - timedelta(seconds=30)
        row.idle_expires_at = utcnow() - timedelta(seconds=60)
        db.session.commit()
    absolute_retry = post(viewer, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(attempt),
    })
    assert absolute_retry.status_code == 401
    assert viewer.get("/api/v1/qr-view/current").status_code == 401
    with app.app_context():
        row = db.session.get(QrViewSession, started.get_json()["session_id"])
        assert row.absolute_expires_at <= utcnow()
    holder = app.test_client()
    renewed = post(holder, f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert renewed.status_code == 200
    assert renewed.get_json()["session_id"] != started.get_json()["session_id"]
    with app.app_context():
        expired_row = db.session.get(QrViewSession, started.get_json()["session_id"])
        assert expired_row.absolute_expires_at <= utcnow()
    shortened = stamp(utcnow() + timedelta(minutes=30))
    changed = post(client, f"/api/v1/qr-shares/{body['id']}/expiry", {
        "expected_version": 1,
        "expires_at": shortened,
    })
    assert changed.status_code == 200, changed.get_json()
    assert changed.get_json()["share"]["expires_at"] == shortened
    with app.app_context():
        share = db.session.get(QrShare, body["id"])
        share.created_at = utcnow() - timedelta(hours=2)
        share.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    current = holder.get("/api/v1/qr-view/current")
    assert current.status_code == 403
    assert current.get_json()["error"] == "qr_expired"
    held = post(app.test_client(), f"/api/v1/qr-shares/{body['id']}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    })
    assert held.status_code == 403
    assert held.get_json()["error"] == "qr_expired"


def test_added_passkey_proof_requires_reauth_and_old_passkey_remains(client, app):
    original, _created, _public = register(client, "追加する人")
    fresh = app.test_client()
    logged, _credential = login(fresh, original)
    assert logged.status_code == 200
    denied = post(fresh, f"/api/v1/auth/passkeys/{logged.get_json()['passkey_id']}/proof/options", {})
    assert denied.status_code == 403
    assert denied.get_json()["error"] == "reauth_required"
    options = post(client, "/api/v1/auth/passkeys/options", {})
    assert options.status_code == 200, options.get_json()
    added = SoftPasskey()
    verified = post(client, "/api/v1/auth/passkeys/verify", {
        "credential": added.register(options.get_json()["public_key"], ORIGIN),
        "envelope": envelope(options.get_json()["prf_input"]),
    })
    assert verified.status_code == 200, verified.get_json()
    blocked, _credential = login(app.test_client(), added)
    assert blocked.status_code == 403
    assert blocked.get_json()["error"] == "passkey_unproven"
    still_old, _credential = login(app.test_client(), original)
    assert still_old.status_code == 200
    listed = client.get("/api/v1/auth/passkeys").get_json()["passkeys"]
    usable = [row for row in listed if row["revoked_at"] is None and row["proven_at"]]
    pending = [row for row in listed if row["revoked_at"] is None and not row["proven_at"]]
    assert len(usable) == 1
    assert len(pending) == 1
    assert pending[0]["id"] == verified.get_json()["passkey_id"]
    original_id = client.get("/api/v1/auth/session").get_json()["passkey_id"]
    last = post(client, f"/api/v1/auth/passkeys/{original_id}/disable", {})
    assert last.status_code == 409
    assert last.get_json()["error"] == "last_passkey"
    proof = post(client, f"/api/v1/auth/passkeys/{verified.get_json()['passkey_id']}/proof/options", {})
    assert proof.status_code == 200, proof.get_json()
    assert "wrapped_key" in proof.get_json()["envelope"]
    proved = post(client, f"/api/v1/auth/passkeys/{verified.get_json()['passkey_id']}/proof/verify", {
        "credential": added.authenticate(proof.get_json()["public_key"], ORIGIN),
    })
    assert proved.status_code == 200, proved.get_json()
    opened, _credential = login(app.test_client(), added)
    assert opened.status_code == 200, opened.get_json()
    again, _credential = login(app.test_client(), original)
    assert again.status_code == 200
    family = client.get("/family")
    transfer = client.get("/transfer")
    assert b'id="unlock-here"' in family.data
    assert b'id="show-preview"' in family.data
    assert b'id="target-confirm"' in transfer.data
    assert b'id="login-here"' in transfer.data


def test_prove_passkey_upgrade_keeps_existing_challenges(app):
    with app.app_context():
        db.create_all()
        apply_schema_upgrades()
        db.session.execute(text("DROP TABLE webauthn_challenges"))
        db.session.execute(text(
            "CREATE TABLE webauthn_challenges ("
            "challenge BLOB NOT NULL, purpose VARCHAR(24) NOT NULL, user_id VARCHAR(36), "
            "flow_binding_hash BLOB NOT NULL, request_options JSON NOT NULL, "
            "expires_at DATETIME NOT NULL, consumed_at DATETIME, id VARCHAR(36) NOT NULL, "
            "created_at DATETIME NOT NULL, PRIMARY KEY (id), "
            "CONSTRAINT ck_webauthn_challenges_purpose CHECK "
            "(purpose IN ('register', 'login', 'reauthenticate', 'add_passkey')), "
            "UNIQUE (challenge))"
        ))
        db.session.execute(text(
            "INSERT INTO webauthn_challenges ("
            "challenge, purpose, user_id, flow_binding_hash, request_options, "
            "expires_at, consumed_at, id, created_at"
            ") VALUES (:challenge, 'login', NULL, :flow, '{}', "
            "'2026-10-16 00:00:00', NULL, '11111111-1111-1111-1111-111111111111', "
            "'2026-10-15 00:00:00')"
        ), {"challenge": b"a" * 32, "flow": b"b" * 32})
        db.session.execute(text(
            "DELETE FROM schema_migrations WHERE version = '20261015_prove_passkey_purpose'"
        ))
        db.session.commit()
        driver = db.session.connection().connection.driver_connection
        apply_schema_upgrades()
        assert db.session.connection().connection.driver_connection is driver
        purpose = db.session.execute(text(
            "SELECT purpose FROM webauthn_challenges "
            "WHERE id = '11111111-1111-1111-1111-111111111111'"
        )).scalar()
        assert purpose == "login"
        definition = db.session.execute(text(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'webauthn_challenges'"
        )).scalar()
        assert "prove_passkey" in definition
        cursor = driver.cursor()
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1
        cursor.close()
        with pytest.raises(IntegrityError):
            db.session.execute(text(
                "INSERT INTO webauthn_challenges ("
                "challenge, purpose, user_id, flow_binding_hash, request_options, "
                "expires_at, consumed_at, id, created_at"
                ") VALUES (:challenge, 'login', 'missing-user', :flow, '{}', "
                "'2026-10-16 00:00:00', NULL, '22222222-2222-2222-2222-222222222222', "
                "'2026-10-15 00:00:00')"
            ), {"challenge": b"c" * 32, "flow": b"d" * 32})
            db.session.commit()
        db.session.rollback()


def test_foreign_keys_restore_outside_an_open_transaction(app):
    with app.app_context():
        db.create_all()
        connection = db.session.connection()
        driver = connection.connection.driver_connection
        assert set_sqlite_foreign_keys(connection, False) == 0
        db.session.execute(text("CREATE TABLE fk_probe (id INTEGER PRIMARY KEY)"))
        db.session.execute(text("INSERT INTO fk_probe (id) VALUES (1)"))
        assert driver.in_transaction
        db.session.execute(text("PRAGMA foreign_keys=ON"))
        cursor = driver.cursor()
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 0
        cursor.close()
        assert set_sqlite_foreign_keys(connection, True) == 1
        cursor = driver.cursor()
        cursor.execute("PRAGMA foreign_keys")
        assert cursor.fetchone()[0] == 1
        cursor.close()
        assert not driver.in_transaction
        assert db.session.connection().connection.driver_connection is driver


def test_proven_at_upgrade_keeps_existing_passkeys_usable(app):
    with app.app_context():
        db.create_all()
        apply_schema_upgrades()
        db.session.execute(text("ALTER TABLE passkey_credentials DROP COLUMN proven_at"))
        db.session.execute(text(
            "DELETE FROM schema_migrations WHERE version = '20261020_passkey_proven_at'"
        ))
        db.session.execute(text(
            "INSERT INTO users (id, created_at, updated_at, webauthn_user_handle, status) "
            "VALUES ('user-proven', '2026-10-15 00:00:00', '2026-10-15 00:00:00', :handle, 'active')"
        ), {"handle": b"h" * 32})
        db.session.execute(text(
            "INSERT INTO passkey_credentials ("
            "id, created_at, user_id, credential_id, public_key, sign_count, transports, "
            "backup_eligible, backup_state, uv_initialized, prf_enabled"
            ") VALUES ("
            "'pk-proven', '2026-10-15 00:00:00', 'user-proven', :cred, :pub, 0, '[]', 0, 0, 0, 1)"
        ), {"cred": b"c" * 16, "pub": b"p" * 16})
        db.session.commit()
        apply_schema_upgrades()
        proven = db.session.execute(text(
            "SELECT proven_at FROM passkey_credentials WHERE id = 'pk-proven'"
        )).scalar()
        assert proven is not None
        assert str(proven).startswith("2026-10-15")


def test_decrypt_confirmed_column_upgrade_keeps_the_table(app):
    with app.app_context():
        db.create_all()
        apply_schema_upgrades()
        db.session.execute(text("ALTER TABLE device_migrations DROP COLUMN decrypt_confirmed_at"))
        db.session.execute(text(
            "DELETE FROM schema_migrations WHERE version = '20261015_migration_decrypt_confirmed'"
        ))
        db.session.commit()
        names = {column["name"] for column in inspect(db.engine).get_columns("device_migrations")}
        assert "decrypt_confirmed_at" not in names
        apply_schema_upgrades()
        names = {column["name"] for column in inspect(db.engine).get_columns("device_migrations")}
        assert "decrypt_confirmed_at" in names
        assert db.session.query(User).count() == 0


def test_browser_and_python_hpke_interoperate(tmp_path):
    first = subprocess.run(
        ["node", "tests/browser_crypto.mjs"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert first.returncode == 0, first.stderr
    vector = json.loads(first.stdout)
    opened = hpke_open(
        b64decode(vector["privateKey"]), b64decode(vector["enc"]),
        b64decode(vector["ciphertext"]), b64decode(vector["aad"]),
    )
    assert opened == vector["plaintext"].encode()
    public, private = hpke_keypair()
    aad = b"hpke-v1|family|ctx|owner|recipient|key|1"
    enc, ciphertext = hpke_seal(public, b"from-python", aad)
    path = tmp_path / "vector.json"
    path.write_text(json.dumps({
        "publicKey": b64encode(public),
        "privateKey": b64encode(private),
        "enc": b64encode(enc),
        "ciphertext": b64encode(ciphertext),
        "aad": b64encode(aad),
        "plaintext": "from-python",
    }), encoding="utf-8")
    second = subprocess.run(
        ["node", "tests/browser_crypto.mjs", str(path)], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert second.returncode == 0, second.stderr
    source = (ROOT / "app/static/js/watashi/view.mjs").read_text(encoding="utf-8")
    assert "sessionStorage" in source
    assert "holdQrSecrets" in source
    assert "location.hash" in source
    assert "wn_qr" not in source
    flows = subprocess.run(
        ["node", "tests/page_flows.mjs"], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert flows.returncode == 0, flows.stderr + flows.stdout
