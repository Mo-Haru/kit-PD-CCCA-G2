import subprocess
from html.parser import HTMLParser
from pathlib import Path


FEATURE_PAGES = ("/answers",)
FORBIDDEN_PHRASES = ("ログインしました", "保存しました", "暗号化しました", "共有しました")


def test_legacy_hello_still_responds(client):
    response = client.get("/hello")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "Hello, World!"


def test_home_explains_the_foundation(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "意思の記録" in body
    assert "未実装" in body
    assert "運営サーバーに回答の内容を読ませない" in body
    assert 'href="/answers"' in body
    assert 'href="/sharing"' in body
    assert 'href="/auth?next=' in body
    assert 'href="/health"' in body


def test_auth_page_is_standalone(client):
    response = client.get("/auth")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert '<body class="account-body">' in body
    assert '<main id="main" class="account-shell">' in body
    assert 'id="register-form"' in body
    assert 'id="login"' in body
    assert "auth-entry.mjs" in body
    assert 'id="privacy-consent"' in body
    assert 'id="account-actions"' not in body
    for shared_layout in ("<header", "<nav", "<footer", 'class="banner"', "header-session.mjs"):
        assert shared_layout not in body

    class AuthMarkup(HTMLParser):
        def __init__(self):
            super().__init__()
            self.open_tags = []
            self.parents = {}

        def handle_starttag(self, tag, attrs):
            element_id = dict(attrs).get("id")
            if element_id:
                self.parents[element_id] = tuple(self.open_tags)
            if tag not in {"meta", "link", "input"}:
                self.open_tags.append((tag, element_id))

        def handle_endtag(self, tag):
            assert self.open_tags and self.open_tags[-1][0] == tag
            self.open_tags.pop()

    markup = AuthMarkup()
    markup.feed(body)
    markup.close()
    assert not markup.open_tags
    assert ("fieldset", "register-fields") in markup.parents["display-name"]
    assert ("fieldset", "register-fields") in markup.parents["privacy-consent"]


def test_common_pages_have_session_header(client):
    for path in ("/", "/answers", "/sharing", "/family", "/family/invite", "/transfer", "/view/layout-check"):
        response = client.get(path)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert 'id="header-account"' in body
        assert 'id="header-session-label"' in body
        assert 'id="header-session-name"' in body
        assert 'id="header-account-link"' in body
        assert 'href="/auth?next=' in body
        assert 'data-account-url="/account"' in body
        assert "header-session.mjs" in body


def test_header_session_display_states():
    result = subprocess.run(
        ["node", "tests/header_session.mjs"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_health_reports_database_connection(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.is_json
    assert response.get_json() == {"status": "ok", "database": "ok"}


def test_feature_pages_are_marked_unimplemented(client):
    for path in FEATURE_PAGES:
        response = client.get(path)
        body = response.get_data(as_text=True)
        assert response.status_code == 200
        assert "未実装" in body
        assert "この操作は実行できません" in body
        assert "<fieldset disabled>" in body
        for phrase in FORBIDDEN_PHRASES:
            assert phrase not in body


def test_feature_pages_name_their_files(client):
    expectations = {
        "/answers": "app/blueprints/answers.py",
    }
    for path, needle in expectations.items():
        body = client.get(path).get_data(as_text=True)
        assert needle in body


def test_unimplemented_actions_reject_post(client):
    for path in (*FEATURE_PAGES, "/sharing", "/answers/demo", "/auth"):
        response = client.post(path)
        assert response.status_code == 405


def test_sharing_uses_real_client_and_requires_explicit_confirmation(client):
    from tests.test_menu import StrictMarkup

    response = client.get("/sharing")
    assert response.status_code == 200
    markup = StrictMarkup(response.get_data(as_text=True))
    markup.module_script("/static/js/watashi/sharing.mjs")
    for element_id in ("share-fields", "confirm-share", "create-qr"):
        assert "disabled" in markup.one_id(element_id).attrs
    assert "hidden" in markup.one_id("qr-output").attrs
    assert "readonly" in markup.one_id("share-url").attrs
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/static/js/watashi/sharing.mjs").status_code == 200


def test_sharing_preview_confirmation_failure_and_stop():
    result = subprocess.run(
        ["node", "tests/sharing_page.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_qr_view_ignores_late_results_after_ending():
    result = subprocess.run(
        ["node", "tests/view_page.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_family_has_dedicated_invitation_and_shared_answer_dialog(client):
    from tests.test_menu import StrictMarkup

    markup = StrictMarkup(client.get("/family").get_data(as_text=True))
    assert not any(element.attrs.get("id") == "login-here" for element in markup.elements)
    assert markup.one_id("unlock-here").attrs["data-unlock-state"] == "locked"
    assert markup.one_id("family-detail").tag == "dialog"
    for button in ("family-stop-top", "family-stop-bottom"):
        assert "button-danger" in markup.one_id(button).attrs["class"].split()
    markup.one_id("invite-qr")
    invite = StrictMarkup(client.get("/family/invite").get_data(as_text=True))
    invite.module_script("/static/js/watashi/family-invitation.mjs")
    assert "disabled" in invite.one_id("accept-invitation").attrs
    assert "hidden" in invite.one_id("invite-confirmation").attrs
    assert not any(element.attrs.get("id") == "share" for element in invite.elements)


def test_family_invitation_login_return_and_confirmation():
    result = subprocess.run(
        ["node", "tests/family_invitation.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_unknown_page_returns_404(client):
    response = client.get("/missing")
    assert response.status_code == 404
    assert "ページが見つかりません" in response.get_data(as_text=True)


def test_css_is_served(client):
    response = client.get("/static/css/style.css")
    assert response.status_code == 200
    assert b"font-family" in response.data


def test_init_db_command_is_registered(app):
    result = app.test_cli_runner().invoke(args=["--help"])
    assert result.exit_code == 0
    assert "init-db" in result.output
