"""大量のアカウント作成・共有作成への対策。"""

import secrets
from datetime import timedelta

import pytest

from app.extensions import db
from app.models import QrShare, User, utcnow
from app.services.support import b64encode, client_key
from config import TestingConfig
from tests.test_protection import connect_pair, hpke_keypair, post, qr_body, register, share_payload, stamp

DAY = 24 * 60 * 60


def limits(app, **changes):
    # 設定クラスの辞書を書き換えないよう、アプリごとに新しい辞書へ差し替える。
    app.config["RATE_LIMITS"] = {**app.config["RATE_LIMITS"], **changes}


def client_from(app, address):
    client = app.test_client()
    client.environ_base["REMOTE_ADDR"] = address
    return client


def users(app):
    with app.app_context():
        return db.session.query(User).count()


@pytest.mark.parametrize(("left", "right", "same"), [
    ("203.0.113.5", "203.0.113.5", True),
    ("203.0.113.5", "203.0.113.6", False),
    ("2001:db8:1:2::1", "2001:db8:1:2:ffff:ffff:ffff:fffe", True),
    ("2001:db8:1:2::1", "2001:db8:1:3::1", False),
    ("::ffff:203.0.113.5", "203.0.113.5", True),
])
def test_client_key_groups_ipv6_by_64(app, left, right, same):
    def key(address):
        with app.test_request_context(environ_base={"REMOTE_ADDR": address}):
            return client_key()
    assert (key(left) == key(right)) is same


def test_registration_is_limited_per_connection(app):
    with app.app_context():
        db.create_all()
    limits(app, register=(2, DAY))
    first = client_from(app, "2001:db8:1:2::1")
    register(first, "一人目")
    # 失敗した検証は数えない。
    assert post(first, "/api/v1/auth/register/verify", {}).status_code == 400
    register(client_from(app, "2001:db8:1:2::2"), "二人目")
    assert users(app) == 2
    # 同じ/64の別アドレスでも3件目は作れない。
    with pytest.raises(AssertionError) as refused:
        register(client_from(app, "2001:db8:1:2::3"), "三人目")
    assert "rate_limited" in str(refused.value)
    assert "この接続元からの新規登録が多すぎます" in str(refused.value)
    assert users(app) == 2
    # 別の接続元からは登録できる。ログインなど他の操作は止めない。
    register(client_from(app, "2001:db8:9:9::1"), "別の回線")
    assert users(app) == 3
    assert post(first, "/api/v1/auth/login/options").status_code == 200


def test_registration_has_a_site_wide_ceiling(app):
    with app.app_context():
        db.create_all()
    limits(app, register_global=(3, 60 * 60))
    for number in range(3):
        register(client_from(app, f"198.51.100.{number + 1}"), f"利用者{number}")
    with pytest.raises(AssertionError) as refused:
        register(client_from(app, "198.51.100.200"), "四人目")
    assert "新規登録を一時的に制限しています" in str(refused.value)
    assert users(app) == 3


def proxied_app(count):
    from app import create_app

    class ProxyConfig(TestingConfig):
        TRUSTED_PROXY_COUNT = count

    application = create_app(ProxyConfig)
    with application.app_context():
        db.create_all()
    limits(application, auth_options=(1, 60 * 60))
    return application


def login_options(client, forwarded):
    return post(client, "/api/v1/auth/login/options", headers={"X-Forwarded-For": forwarded})


def test_forwarded_address_is_used_only_behind_a_trusted_proxy():
    behind_proxy = proxied_app(1)
    proxy = client_from(behind_proxy, "10.0.0.2")
    assert login_options(proxy, "203.0.113.5").status_code == 200
    assert login_options(proxy, "203.0.113.5").status_code == 429
    # 別の利用者は、同じプロキシを通っていても止められない。
    assert login_options(proxy, "203.0.113.6").status_code == 200
    # 利用者が先頭に偽の値を足しても、プロキシが付けた最後の値で数える。
    assert login_options(proxy, "192.0.2.1, 203.0.113.5").status_code == 429

    direct = proxied_app(0)
    attacker = client_from(direct, "203.0.113.9")
    assert login_options(attacker, "192.0.2.1").status_code == 200
    # プロキシを信用しない設定では、ヘッダーを変えても同じ接続元として数える。
    assert login_options(attacker, "192.0.2.2").status_code == 429


@pytest.mark.parametrize(("value", "expected"), [("", 0), ("  ", 0), ("1", 1), ("2", 2)])
def test_proxy_count_from_environment(monkeypatch, value, expected):
    from config import proxy_count_from_environ
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", value)
    assert proxy_count_from_environ() == expected


@pytest.mark.parametrize("value", ["-1", "6", "two"])
def test_invalid_proxy_count_stops_startup(monkeypatch, value):
    from config import proxy_count_from_environ
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", value)
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_COUNT"):
        proxy_count_from_environ()


def create_qr(client, owner_id, mode="emergency", token=None):
    public, _ = hpke_keypair()
    expires = stamp(utcnow() + timedelta(days=1)) if mode == "timed" else None
    body, _ = qr_body(owner_id, mode, public, token or secrets.token_bytes(32), b"dummy", expires)
    return post(client, "/api/v1/qr-shares", body), body["id"]


def test_an_account_keeps_at_most_twenty_active_shares(app, client):
    with app.app_context():
        db.create_all()
    assert app.config["SHARE_ACTIVE_LIMIT"] == 20
    _passkey, owner, _public = register(client, "共有する人")
    family_public, _ = hpke_keypair()
    _family, _p, family_body, connection_id = connect_pair(app, client, None, "家族", family_public)
    shared, _id, _aad = share_payload(client, connection_id, family_body["user_id"],
                                      family_body["encryption_key_id"], family_public, b"{}")
    assert shared.status_code == 200, shared.get_json()
    ids = []
    for _ in range(19):
        created, share_id = create_qr(client, owner["user_id"], "timed")
        assert created.status_code == 200, created.get_json()
        ids.append(share_id)
    # QRと家族共有の合計で20件。
    refused, _ = create_qr(client, owner["user_id"])
    assert refused.status_code == 409
    assert refused.get_json()["error"] == "share_limit"
    assert "20件" in refused.get_json()["message"]
    family_refused, _id, _aad = share_payload(client, connection_id, family_body["user_id"],
                                              family_body["encryption_key_id"], family_public, b"{}")
    assert family_refused.status_code == 409
    # 停止・期限切れ・使用済みは数えない。
    assert post(client, f"/api/v1/qr-shares/{ids[0]}/stop").status_code == 200
    with app.app_context():
        expired = db.session.get(QrShare, ids[1])
        expired.created_at = utcnow() - timedelta(days=2)
        expired.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    token = secrets.token_bytes(32)
    emergency, emergency_id = create_qr(client, owner["user_id"], token=token)
    assert emergency.status_code == 200
    assert create_qr(client, owner["user_id"])[0].status_code == 200
    assert create_qr(client, owner["user_id"])[0].status_code == 409
    assert post(app.test_client(), f"/api/v1/qr-shares/{emergency_id}/start", {
        "start_token": b64encode(token), "attempt_id": b64encode(secrets.token_bytes(32)),
    }).status_code == 200
    assert create_qr(client, owner["user_id"])[0].status_code == 200
    # 他のアカウントの枠には影響しない。
    other = app.test_client()
    _p2, other_body, _pub2 = register(other, "別の人")
    assert create_qr(other, other_body["user_id"])[0].status_code == 200


def test_share_creation_is_rate_limited_per_account(app, client):
    with app.app_context():
        db.create_all()
    limits(app, share_create=(2, DAY))
    _passkey, owner, _public = register(client, "作りすぎる人")
    first, first_id = create_qr(client, owner["user_id"])
    assert first.status_code == 200
    assert post(client, f"/api/v1/qr-shares/{first_id}/stop").status_code == 200
    assert create_qr(client, owner["user_id"])[0].status_code == 200
    # 停止して枠を空けても、作成回数の上限で止まる。
    third = create_qr(client, owner["user_id"])[0]
    assert third.status_code == 429
    assert "共有の作成が多すぎます" in third.get_json()["message"]
    # 同じ接続元の別アカウントは止めない。
    other = app.test_client()
    _p2, other_body, _pub2 = register(other, "別の人")
    assert create_qr(other, other_body["user_id"])[0].status_code == 200
