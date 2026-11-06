"""LINE通知（小橋担当の notice/notice.py を本体に取り込んだもの）のテスト。テストの文章には個人情報を使わない。"""
import base64
import hashlib
import hmac
import json
from datetime import datetime
from unittest.mock import patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from app.services import line

USER = "U" + "1" * 32


@pytest.fixture
def app(app):
    app.config.update(
        LINE_CHANNEL_ACCESS_TOKEN="test-token",
        LINE_CHANNEL_SECRET="test-secret",
        LINE_TARGET_USER_ID=USER,
        LINE_DAILY_TIME="18:00",
        APP_BASE_URL="https://example.test",
    )
    return app


def event(kind, **fields):
    if kind == "message":
        fields["message"]["quoteToken"] = "test-quote-token"
    return dict(type=kind, timestamp=0, mode="active",
                source={"type": "user", "userId": USER},
                replyToken="reply-token", webhookEventId="test-event",
                deliveryContext={"isRedelivery": False}, **fields)


def post(client, item=None, raw=None, signature=None):
    body = raw if raw is not None else json.dumps(
        {"destination": "U" + "2" * 32, "events": [] if item is None else [item]}, ensure_ascii=False)
    signature = signature or base64.b64encode(hmac.new(
        b"test-secret", body.encode(), hashlib.sha256).digest()).decode()
    return client.post("/line/callback", data=body.encode(),
                       headers={"X-Line-Signature": signature}, content_type="application/json")


@patch.object(line.MessagingApi, "push_message_with_http_info")
def test_push_recipient_buttons_and_answer_link(send, app):
    with app.app_context():
        line.send_question()
    req = send.call_args.args[0].to_dict()
    assert req["to"] == USER
    message = req["messages"][0]
    assert "https://example.test/answers" in message["text"]
    buttons = message["quickReply"]["items"]
    assert buttons[0]["action"] == {"type": "uri", "label": "アプリで回答する", "uri": "https://example.test/answers"}
    assert [b["action"]["data"] for b in buttons[1:]] == ["answer:q1:hobby", "answer:q1:family", "answer:q1:undecided"]
    assert all(b["action"]["type"] == "postback" for b in buttons[1:])


def test_answer_link_defaults_to_the_app_origin(app):
    app.config["APP_BASE_URL"] = None
    with app.app_context():
        assert line.answer_url() == "http://localhost/answers"


@patch.object(line.MessagingApi, "reply_message_with_http_info")
def test_text_echo(send, client):
    res = post(client, event("message", message={"type": "text", "id": "1", "text": "接続テスト"}))
    assert res.status_code == 200
    req = send.call_args.args[0]
    assert req.reply_token == "reply-token"
    assert req.messages[0].text == "接続テスト"


@patch.object(line.MessagingApi, "reply_message_with_http_info")
def test_question_command(send, client):
    res = post(client, event("message", message={"type": "text", "id": "1", "text": "質問"}))
    assert res.status_code == 200
    assert len(send.call_args.args[0].messages[0].quick_reply.items) == 4


@patch.object(line.MessagingApi, "reply_message_with_http_info")
def test_all_choices_reply_without_saving(send, client):
    for key, (_, expected) in line.CHOICES.items():
        res = post(client, event("postback", postback={"data": f"answer:q1:{key}"}))
        assert res.status_code == 200
        text = send.call_args.args[0].messages[0].text
        assert expected in text
        assert "保存していません" in text and "https://example.test/answers" in text


@patch.object(line.MessagingApi, "reply_message_with_http_info")
def test_unknown_choice(send, client):
    res = post(client, event("postback", postback={"data": "answer:q1:invalid"}))
    assert res.status_code == 200
    assert "もう一度" in send.call_args.args[0].messages[0].text


@patch.object(line.MessagingApi, "reply_message_with_http_info")
def test_invalid_signature_no_reply(send, client):
    assert post(client, signature="invalid").status_code == 400
    send.assert_not_called()


def test_missing_signature(client):
    assert client.post("/line/callback", data="{}").status_code == 400


def test_signed_invalid_json(client):
    assert post(client, raw="{").status_code == 400


def test_verification_empty_events(client):
    assert post(client).status_code == 200


def test_not_configured_is_unavailable(app, client):
    app.config["LINE_CHANNEL_SECRET"] = None
    assert post(client).status_code == 503


@patch.object(line.MessagingApi, "reply_message_with_http_info", side_effect=line.ApiException(status=429))
def test_api_failure(send, client):
    res = post(client, event("message", message={"type": "text", "id": "1", "text": "テスト"}))
    assert res.status_code == 502


def test_notification_time_in_japan(app):
    job = line.build_scheduler(app, BackgroundScheduler).get_job("daily_question")
    before = datetime(2026, 11, 6, 17, 59, tzinfo=line.JST)
    after = datetime(2026, 11, 6, 18, 1, tzinfo=line.JST)
    assert job.trigger.get_next_fire_time(None, before) == datetime(2026, 11, 6, 18, 0, tzinfo=line.JST)
    assert job.trigger.get_next_fire_time(None, after) == datetime(2026, 11, 7, 18, 0, tzinfo=line.JST)
    assert job.max_instances == 1


@patch.object(line.MessagingApi, "push_message_with_http_info")
def test_scheduled_job_sends_inside_the_app(send, app):
    job = line.build_scheduler(app, BackgroundScheduler).get_job("daily_question")
    job.func()
    assert send.call_args.args[0].to == USER


def test_invalid_settings(app):
    app.config["LINE_DAILY_TIME"] = "25:61"
    with pytest.raises(ValueError):
        line.build_scheduler(app, BackgroundScheduler)
    app.config["LINE_TARGET_USER_ID"] = ""
    with app.app_context(), pytest.raises(ValueError):
        line.send_question()


@patch.object(line, "send_question")
@patch.object(line, "build_scheduler")
def test_manual_send_does_not_start_scheduler(build, send, app):
    result = app.test_cli_runner().invoke(args=["line-send"])
    assert result.exit_code == 0, result.output
    send.assert_called_once()
    build.assert_not_called()


@patch.object(line, "build_scheduler")
def test_schedule_command_starts_the_scheduler(build, app):
    result = app.test_cli_runner().invoke(args=["line-schedule"])
    assert result.exit_code == 0, result.output
    build.return_value.start.assert_called_once()


def test_schedule_command_requires_a_recipient(app):
    app.config["LINE_TARGET_USER_ID"] = None
    result = app.test_cli_runner().invoke(args=["line-schedule"])
    assert result.exit_code != 0
    assert "LINE_TARGET_USER_ID" in result.output
