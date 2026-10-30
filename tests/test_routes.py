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
    for path in ("/", "/answers", "/sharing", "/family", "/family/share", "/family/invite", "/transfer", "/view/layout-check"):
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
    markup.one_id("qr-image")
    assert response.headers["Cache-Control"] == "no-store"
    assert client.get("/static/js/watashi/sharing.mjs").status_code == 200


def test_share_link_qr_image_is_drawn_locally():
    result = subprocess.run(
        ["node", "tests/qr_share_view.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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
    # 家族の画面に「回答の暗号化を解除」のボタンはない。回答を開くときに、その場で本人確認をする。
    for removed in ("unlock-here", "invite-unlock"):
        assert not any(element.attrs.get("id") == removed for element in markup.elements)
    assert "hidden" in markup.one_id("family-detail-unlock").attrs
    assert "パスキーで本人確認して表示" in client.get("/family").get_data(as_text=True)
    assert markup.one_id("family-detail").tag == "dialog"
    # 配信停止と閉じるは、右上に並べる。下部のボタンはない。
    assert "button-danger" in markup.one_id("family-stop").attrs["class"].split()
    actions = markup.one_id("family-stop").ancestors[-1]
    assert actions is markup.one_id("family-close").ancestors[-1]
    assert markup.one_id("family-detail-title").ancestors[-1] is actions.ancestors[-1], "title and buttons share the header row"
    for removed in ("family-stop-bottom", "family-close-bottom", "family-stop-top", "family-close-top"):
        assert not any(element.attrs.get("id") == removed for element in markup.elements)
    detail = markup.one_id("family-detail")
    assert not any(detail in element.ancestors and "share-dialog-footer" in (element.attrs.get("class") or "").split()
                   for element in markup.elements)
    markup.one_id("invite-qr")
    # 共有の作成は別の画面。一覧の画面には、開始のボタンだけを置く。
    assert markup.one_id("share-start").tag == "button"
    for form_id in ("show-preview", "share", "confirm-share", "expires-at", "connection"):
        assert not any(element.attrs.get("id") == form_id for element in markup.elements)
    # ダッシュボード：上に4つの数、下にカード（家族・招待・共有したもの・共有されたもの）。
    for stat in ("stat-family", "stat-sharing", "stat-received", "stat-attention"):
        markup.one_id(stat)
    assert markup.one_id("stat-attention-tile").attrs["data-attention"] == "false"
    # 家族のカードは2枚分の幅で、右上に「編集」「家族の招待」。招待のカードはない。
    wide = markup.one_id("connections").ancestors[-1]
    assert "dash-card-wide" in wide.attrs["class"].split()
    for button_id in ("family-edit", "invite-start"):
        assert wide in markup.one_id(button_id).ancestors
    assert markup.one_id("invite-dialog").tag == "dialog"
    assert wide not in markup.one_id("invite-open").ancestors
    assert not any(element.attrs.get("aria-labelledby") == "dash-invite-title" for element in markup.elements)
    for list_id in ("connections", "incoming", "shares", "received"):
        assert any("dash-card" in (ancestor.attrs.get("class") or "").split() for ancestor in markup.one_id(list_id).ancestors)
    markup.one_id("shares")
    markup.one_id("received")
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


def test_family_confirm_page_waits_for_explicit_confirmation(client):
    from tests.test_menu import StrictMarkup

    response = client.get("/family/confirm")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    markup = StrictMarkup(response.get_data(as_text=True))
    markup.module_script("/static/js/watashi/family-confirm.mjs")
    assert "disabled" in markup.one_id("confirm-link").attrs
    for hidden in ("confirm-guest", "confirm-action"):
        assert "hidden" in markup.one_id(hidden).attrs
    invite = StrictMarkup(client.get("/family/invite").get_data(as_text=True))
    assert "hidden" in invite.one_id("confirmation-output").attrs
    family = StrictMarkup(client.get("/family").get_data(as_text=True))
    assert "hidden" in family.one_id("confirmation-output").attrs
    assert client.post("/family/confirm").status_code == 405


def test_family_confirm_link_page():
    result = subprocess.run(
        ["node", "tests/family_confirm_page.mjs"], cwd=Path(__file__).resolve().parents[1],
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


def test_family_share_page_is_a_stepwise_flow(client):
    from tests.test_menu import StrictMarkup

    response = client.get("/family/share")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    markup = StrictMarkup(response.get_data(as_text=True))
    markup.module_script("/static/js/watashi/family-share.mjs")
    order = ["step-unlock", "step-select", "step-target", "step-confirm", "step-done"]
    positions = [markup.elements.index(markup.one_id(step)) for step in order]
    assert positions == sorted(positions)
    for step in order[1:]:
        assert "hidden" in markup.one_id(step).attrs
    assert "hidden" not in markup.one_id("step-unlock").attrs
    for control in ("unlock-here", "step-next", "share", "confirm-share"):
        assert "disabled" in markup.one_id(control).attrs
    assert markup.one_id("back-to-family").attrs["href"] == "/family"
    # 戻るリンクは、見出しと同じ行（右上）にある。
    heading = markup.one_id("share-title").ancestors[-1]
    assert heading in markup.one_id("back-to-family").ancestors and "share-heading" in heading.attrs["class"].split()
    markup.one_id("expires-unlimited")
    scope_radios = [element for element in markup.elements
                    if element.tag == "input" and element.attrs.get("name") == "scope"]
    assert len(scope_radios) == 3 and not any("checked" in radio.attrs for radio in scope_radios), "範囲は最初は未選択"
    body = response.get_data(as_text=True)
    for title in ("ロックを解除してください。", "共有範囲を選択してください。", "誰にいつまで共有しますか？",
                  "共有するには、回答の暗号化を解除する必要があります。"):
        assert title in body


def test_family_share_page_flow():
    result = subprocess.run(
        ["node", "tests/family_share_page.mjs"], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
