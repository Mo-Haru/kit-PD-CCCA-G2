"""JSON本文の上限は、Content-Lengthの有無にかかわらず、本文を読み切って解析する前に413で止める。"""

import io
import json

import pytest

from app.extensions import db

LIMIT = 800_000


class CountingStream(io.BytesIO):
    """サーバーが読んだバイト数を数える入力。"""

    def __init__(self, data):
        super().__init__(data)
        self.consumed = 0

    def read(self, size=-1):
        chunk = super().read(size)
        self.consumed += len(chunk)
        return chunk

    def readline(self, size=-1):
        chunk = super().readline(size)
        self.consumed += len(chunk)
        return chunk


@pytest.fixture
def api(app):
    with app.app_context():
        db.create_all()
    return app.test_client()


def send(client, body, *, length):
    from werkzeug.test import EnvironBuilder

    stream = CountingStream(body)
    # Content-Lengthなし（chunked相当）は、本文の終わりをサーバーが判断する（wsgi.input_terminated）。
    builder = EnvironBuilder(path="/api/v1/auth/register/options", method="POST", input_stream=stream,
                             headers={"Content-Type": "application/json"},
                             environ_overrides={"wsgi.input_terminated": True})
    environ = builder.get_environ()
    if not length:
        # テスト用クライアントは入力の長さから自動で付けるので、ここで外す。
        environ.pop("CONTENT_LENGTH", None)
    assert ("CONTENT_LENGTH" in environ) is length
    # Flaskのテストクライアントは環境から要求を組み立て直してContent-Lengthを付け直すため、WSGIで直接呼ぶ。
    from werkzeug.test import run_wsgi_app
    from werkzeug.wrappers import Response

    app_iter, status, headers = run_wsgi_app(client.application, environ, buffered=True)
    return Response(app_iter, status, headers), stream


@pytest.mark.parametrize("length", [True, False], ids=["content-length", "no-content-length"])
def test_oversized_json_is_rejected_before_reading_it_all(api, length):
    body = json.dumps({"display_name": "x" * 900_000}).encode()
    response, stream = send(api, body, length=length)
    assert response.status_code == 413, response.get_data(as_text=True)[:200]
    assert json.loads(response.get_data()) == {"error": "too_large", "message": "送信データが大きすぎます。"}
    # 上限を少し超えた所で読むのをやめる（本文全体の90万バイトは読み込まない）。
    assert stream.consumed <= LIMIT + 64 * 1024, stream.consumed


@pytest.mark.parametrize("length", [True, False], ids=["content-length", "no-content-length"])
def test_bodies_within_the_limit_still_work(api, length):
    response, _stream = send(api, json.dumps({"display_name": "利用者"}).encode(), length=length)
    assert response.status_code == 200, response.get_data(as_text=True)[:200]


@pytest.mark.parametrize("length", [True, False], ids=["content-length", "no-content-length"])
def test_a_body_of_exactly_the_limit_is_not_rejected_for_size(api, length):
    # ちょうど上限のバイト数なら、大きさでは断らない（表示名が長すぎるので400になる）。
    body = json.dumps({"display_name": ""}).encode()
    body = json.dumps({"display_name": "x" * (LIMIT - len(body))}).encode()
    assert len(body) == LIMIT
    response, _stream = send(api, body, length=length)
    assert response.status_code == 400
    assert json.loads(response.get_data())["error"] == "invalid_display_name"


def test_oversized_non_json_body_is_rejected_too(api):
    response, _stream = send(api, b"x" * 900_000, length=False)
    assert response.status_code == 413


def test_the_app_wide_limit_matches_json_body(app):
    assert app.config["MAX_CONTENT_LENGTH"] == LIMIT
