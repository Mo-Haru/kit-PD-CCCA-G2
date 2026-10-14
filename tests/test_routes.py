FEATURE_PAGES = ("/answers", "/sharing", "/auth")
FORBIDDEN_PHRASES = ("ログインしました", "保存しました", "暗号化しました", "共有しました")


def test_home_explains_the_foundation(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "意思の記録" in body
    assert "未実装" in body
    assert "運営サーバーに回答の内容を読ませない" in body
    assert 'href="/answers"' in body
    assert 'href="/sharing"' in body
    assert 'href="/auth"' in body
    assert 'href="/health"' in body


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
        "/sharing": "app/blueprints/sharing.py",
        "/auth": "app/blueprints/auth.py",
    }
    for path, needle in expectations.items():
        body = client.get(path).get_data(as_text=True)
        assert needle in body


def test_unimplemented_actions_reject_post(client):
    for path in FEATURE_PAGES:
        response = client.post(path)
        assert response.status_code == 405


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
