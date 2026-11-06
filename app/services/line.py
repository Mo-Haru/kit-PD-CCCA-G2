"""LINE通知（小橋担当の notice/notice.py を本体に取り込んだもの）。

毎日決まった時刻に、設定した1人へ質問を送り、アプリの回答画面（/answers）へ案内する。
LINEのボタンで選んだ回答は保存しない。サーバーが回答の平文を受け取って残さないため、
保存したい回答は、アプリの回答画面で端末内で暗号化して保存してもらう。
秘密情報（チャネルのアクセストークン・シークレット）は .env にだけ置く。
"""

import re
from zoneinfo import ZoneInfo

import click
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from flask import current_app
from flask.cli import with_appcontext
from linebot.v3 import WebhookHandler
from linebot.v3.messaging import (
    ApiClient, ApiException, Configuration, MessagingApi,
    PostbackAction, PushMessageRequest, QuickReply, QuickReplyItem,
    ReplyMessageRequest, TextMessage, URIAction,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent
from urllib3.exceptions import HTTPError

JST = ZoneInfo("Asia/Tokyo")
TIME_PATTERN = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")
USER_ID_PATTERN = re.compile(r"U[0-9a-f]{32}")

# dataには選択肢の識別子を使う。表示文は変更してもよい。
CHOICES = {
    "hobby": ("趣味の時間", "趣味の時間を大切にしたい、という回答ですね。"),
    "family": ("家族との時間", "家族との時間を大切にしたい、という回答ですね。"),
    "undecided": ("まだ決めていない", "今すぐ決めなくても大丈夫です。自分のペースで考えてみましょう。"),
}
NOT_SAVED = "※LINEで選んだ回答は保存していません。残したいときは、アプリの回答画面で答えてください。"


def answer_url() -> str:
    """通知から案内する回答画面のURL。"""
    base = (current_app.config.get("APP_BASE_URL") or current_app.config["WEBAUTHN_ORIGIN"]).rstrip("/")
    return f"{base}/answers"


def question_message() -> TextMessage:
    """質問と選択ボタン、回答画面へのボタンを作る。"""
    url = answer_url()
    return TextMessage(
        text="【私ノート】\nこれからの生活で大切にしたい時間は？\n"
             "下のボタンから気軽に選んでみてください。\n"
             f"じっくり答えて残すときは、アプリの回答画面へ：\n{url}",
        quick_reply=QuickReply(items=[
            QuickReplyItem(action=URIAction(label="アプリで回答する", uri=url)),
            *(
                QuickReplyItem(action=PostbackAction(
                    label=label,
                    data=f"answer:q1:{key}",
                    display_text=f"回答：{label}",
                ))
                for key, (label, _) in CHOICES.items()
            ),
        ]),
    )


def answer_reply(data: str) -> str:
    prefix = "answer:q1:"
    key = data[len(prefix):] if data.startswith(prefix) else ""
    if key not in CHOICES:
        return "選択肢を確認できませんでした。「質問」と送って、もう一度選んでください。"
    return f"回答ありがとうございます。\n{CHOICES[key][1]}\n{NOT_SAVED}\n{answer_url()}"


def _configuration() -> Configuration:
    return Configuration(access_token=current_app.config.get("LINE_CHANNEL_ACCESS_TOKEN") or "")


def reply(reply_token: str, message: TextMessage) -> None:
    with ApiClient(_configuration()) as client:
        MessagingApi(client).reply_message_with_http_info(
            ReplyMessageRequest(reply_token=reply_token, messages=[message]),
            _request_timeout=10,
        )


def validate_push_settings() -> None:
    if not current_app.config.get("LINE_CHANNEL_ACCESS_TOKEN"):
        raise ValueError("LINE_CHANNEL_ACCESS_TOKENを.envに設定してください。")
    if not USER_ID_PATTERN.fullmatch(current_app.config.get("LINE_TARGET_USER_ID") or ""):
        raise ValueError("LINE_TARGET_USER_IDに送信先のユーザーID（Uから始まる33文字）を設定してください。")


def send_question() -> None:
    """ユーザーの発言を待たず、設定した1人へ質問を送る。"""
    validate_push_settings()
    with ApiClient(_configuration()) as client:
        MessagingApi(client).push_message_with_http_info(
            PushMessageRequest(to=current_app.config["LINE_TARGET_USER_ID"], messages=[question_message()]),
            _request_timeout=10,
        )


def build_scheduler(app, scheduler_class=BlockingScheduler):
    """毎日の通知（日本時間）を予約する。1プロセスだけで動かす（二重に動かすと通知も重複する）。"""
    daily = app.config.get("LINE_DAILY_TIME") or ""
    if not TIME_PATTERN.fullmatch(daily):
        raise ValueError("DAILY_NOTIFICATION_TIMEは18:00のように24時間表記で設定してください。")
    hour, minute = map(int, daily.split(":"))

    def scheduled_question():
        with app.app_context():
            try:
                send_question()
                app.logger.info("定期通知の送信要求がLINEに受け付けられました。")
            except ApiException as exc:
                app.logger.error("定期通知のAPIエラー: HTTP %s", exc.status)
            except HTTPError:
                app.logger.error("定期通知の通信エラー。スマートフォンで受信状況を確認してください。")

    scheduler = scheduler_class(timezone=JST)
    scheduler.add_job(
        scheduled_question,
        trigger=CronTrigger(hour=hour, minute=minute, timezone=JST),
        id="daily_question",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    return scheduler


_handlers: dict[str, WebhookHandler] = {}


def webhook_handler() -> WebhookHandler:
    """チャネルシークレットごとの受信処理。署名の検証はここで行う。"""
    secret = current_app.config.get("LINE_CHANNEL_SECRET") or ""
    if secret not in _handlers:
        handler = WebhookHandler(secret)

        @handler.add(MessageEvent, message=TextMessageContent)
        def echo(event):
            # 「質問」と送ると、通知時刻を待たずに質問を確認できる。
            text = event.message.text.strip()
            reply(event.reply_token, question_message() if text == "質問" else TextMessage(text=event.message.text))

        @handler.add(PostbackEvent)
        def answer(event):
            reply(event.reply_token, TextMessage(text=answer_reply(event.postback.data)))

        _handlers[secret] = handler
    return _handlers[secret]


def configured() -> bool:
    return bool(current_app.config.get("LINE_CHANNEL_ACCESS_TOKEN") and current_app.config.get("LINE_CHANNEL_SECRET"))


@click.command("line-send")
@with_appcontext
def line_send_command():
    """設定した相手へ、LINEで質問を1回送る。"""
    try:
        send_question()
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    except ApiException as exc:
        raise click.ClickException(f"送信に失敗しました。HTTP {exc.status}") from None
    except HTTPError:
        raise click.ClickException("通信に失敗しました。再実行前にLINEの受信状況を確認してください。") from None
    click.echo("送信要求がLINEに受け付けられました。スマートフォンで受信を確認してください。")


@click.command("line-schedule")
@with_appcontext
def line_schedule_command():
    """毎日の定期通知を動かす（止めるまで動き続ける）。Webサーバーとは別に、1つだけ起動する。"""
    app = current_app._get_current_object()
    try:
        validate_push_settings()
        scheduler = build_scheduler(app)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from None
    click.echo(f"定期通知: 毎日 {app.config['LINE_DAILY_TIME']}（日本時間）。止めるときは Ctrl+C。")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
