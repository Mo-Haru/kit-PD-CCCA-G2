import subprocess
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.extensions import db
from tests.test_protection import register


class Elements(HTMLParser):
    def __init__(self, body):
        super().__init__()
        self.by_id = {}
        self.feed(body)

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.by_id[attributes["id"]] = attributes


def test_account_requires_login_and_returns_to_account(client):
    response = client.get("/account?tab=passkeys")
    assert response.status_code == 302
    target = urlsplit(response.location)
    assert target.path == "/auth"
    assert parse_qs(target.query)["next"] == ["/account?tab=passkeys"]
    assert response.headers["Cache-Control"] == "no-store"


def test_account_has_header_and_management_but_no_signup(app, client):
    with app.app_context():
        db.create_all()
    register(client, "設定画面の検証")
    response = client.get("/account")
    body = response.get_data(as_text=True)
    elements = Elements(body).by_id
    assert response.status_code == 200
    assert '<header id="site-header">' in body
    assert "header-account" in elements
    assert "account-settings" in elements
    assert "unlock" in elements and "logout" in elements
    assert "account-primary" in elements["unlock"]["class"].split()
    assert "account-secondary" in elements["logout"]["class"].split()
    assert "account-tonal" in elements["add-passkey"]["class"].split()
    assert elements["confirm-last"]["type"] == "checkbox"
    assert "register-form" not in elements
    assert "privacy-consent" not in elements
    assert elements["header-account-link"]["data-account-url"] == "/account"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("target", [
    "/", "/family", "/family?tab=received&sort=newest", "/account", "/account?tab=passkeys",
    "/view/layout-check", "/answers?section=%E7%AB%A0",
])
def test_login_keeps_local_return_path(client, target):
    response = client.get("/auth", query_string={"next": target})
    assert Elements(response.get_data(as_text=True)).by_id["auth-entry"]["data-return-url"] == target


@pytest.mark.parametrize("target", [
    "", "https://evil.example/path", "http://localhost/family", "//evil.example/path",
    "///evil.example", "javascript:alert(1)", "family", "/\\evil.example", "/%5cevil.example",
    "/%2fevil.example", "/\r\n//evil.example", "/%0d%0a//evil.example", "/auth", "/auth/",
    "/auth?next=/family", "/%61uth",
])
def test_login_rejects_unsafe_or_looping_return_path(client, target):
    response = client.get("/auth", query_string={"next": target})
    assert response.status_code == 200
    assert Elements(response.get_data(as_text=True)).by_id["auth-entry"]["data-return-url"] == "/"


def test_default_return_is_home_and_server_return_does_not_include_fragments(client):
    response = client.get("/auth")
    assert Elements(response.get_data(as_text=True)).by_id["auth-entry"]["data-return-url"] == "/"
    response = client.get("/auth", query_string={"next": "/family/invite#token=fixture-only"})
    assert Elements(response.get_data(as_text=True)).by_id["auth-entry"]["data-return-url"] == "/family/invite"


def test_header_login_keeps_source_path_and_query(client):
    response = client.get("/family?tab=received&sort=newest")
    link = Elements(response.get_data(as_text=True)).by_id["header-account-link"]
    target = urlsplit(link["href"])
    assert target.path == "/auth"
    assert parse_qs(target.query)["next"] == ["/family?tab=received&sort=newest"]
    assert not target.fragment
    assert link["data-account-url"] == "/account"


def test_registration_notice_and_frontend_consent(client):
    body = client.get("/auth").get_data(as_text=True)
    elements = Elements(body).by_id
    consent = elements["privacy-consent"]
    assert consent["type"] == "checkbox"
    assert "required" in consent
    assert "checked" not in consent
    assert "disabled" in elements["register"]
    assert "利用者の端末内で暗号化" in body
    assert "運営による復元はできません" in body
    assert "同期済みのパスキー" in body
    assert "表示名は暗号化の対象外" in body


def test_registration_field_keeps_native_labels_and_constraints(client):
    from tests.test_menu import StrictMarkup

    markup = StrictMarkup(client.get("/auth").get_data(as_text=True))
    field = markup.one_id("display-name")
    wrapper = field.ancestors[-1]
    assert "account-field" in wrapper.attrs["class"].split()
    labels = [element for element in markup.tagged("label") if wrapper in element.ancestors]
    assert len(labels) == 1 and labels[0].attrs["for"] == "display-name"
    assert field.attrs["aria-describedby"] == "display-name-help"
    assert field.attrs["maxlength"] == "100"
    assert field.attrs["autocomplete"] == "nickname"
    assert "required" in field.attrs
    assert wrapper in markup.one_id("display-name-help").ancestors


def test_auth_entry_consent_and_redirect_behavior():
    result = subprocess.run(
        ["node", "--experimental-vm-modules", "tests/auth_entry.mjs"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
