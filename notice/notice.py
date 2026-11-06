"""LINEで届く私ノートの試作。テストの文章には個人情報を使わない。"""
import argparse
import logging
import os
import re
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient, ApiException, Configuration, MessagingApi,
    PostbackAction, PushMessageRequest, QuickReply, QuickReplyItem,
    ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, PostbackEvent, TextMessageContent
from urllib3.exceptions import HTTPError

load_dotenv()
TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
TARGET_USER_ID = os.getenv("LINE_TARGET_USER_ID", "").strip()
DAILY_TIME = os.getenv("DAILY_NOTIFICATION_TIME", "18:00").strip()
SCHEDULE_SETTING = os.getenv("ENABLE_SCHEDULE", "true").strip().lower()
JST = ZoneInfo("Asia/Tokyo")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
handler = WebhookHandler(SECRET)
configuration = Configuration(access_token=TOKEN)

# dataには選択肢の識別子を使う。表示文は変更してもよい。
CHOICES = {
    "hobby": ("趣味の時間", "趣味の時間を大切にしたい、という回答ですね。"),
    "family": ("家族との時間", "家族との時間を大切にしたい、という回答ですね。"),
    "undecided": ("まだ決めていない", "今すぐ決めなくても大丈夫です。自分のペースで考えてみましょう。"),
}


def question_message():
    """テスト用の質問と選択ボタンを作る。回答は保存しない。"""
    return TextMessage(
        text="【私ノート・動作確認】\nこれからの生活で大切にしたい時間は？\n"
             "下のボタンから選んでください。テスト回答で構いません。",
        quick_reply=QuickReply(items=[
            QuickReplyItem(action=PostbackAction(
                label=label,
                data=f"answer:q1:{key}",
                display_text=f"回答：{label}",
            ))
            for key, (label, _) in CHOICES.items()
        ]),
    )


def reply(reply_token, message):
    with ApiClient(configuration) as client:
        MessagingApi(client).reply_message_with_http_info(
            ReplyMessageRequest(reply_token=reply_token, messages=[message]),
            _request_timeout=10,
        )


def validate_push_settings():
    if not TOKEN:
        raise ValueError("LINE_CHANNEL_ACCESS_TOKENを.envに設定してください。")
    if not re.fullmatch(r"U[0-9a-f]{32}", TARGET_USER_ID):
        raise ValueError("LINE_TARGET_USER_IDに送信先のユーザーID（Uから始まる33文字）を設定してください。")


def send_question():
    """ユーザーの発言を待たず、設定した1人へ質問を送る。"""
    validate_push_settings()
    with ApiClient(configuration) as client:
        MessagingApi(client).push_message_with_http_info(
            PushMessageRequest(to=TARGET_USER_ID, messages=[question_message()]),
            _request_timeout=10,
        )


def scheduled_question():
    try:
        send_question()
        app.logger.info("定期通知の送信要求がLINEに受け付けられました。")
    except ApiException as exc:
        app.logger.error("定期通知のAPIエラー: HTTP %s", exc.status)
    except HTTPError:
        app.logger.error("定期通知の通信エラー。スマートフォンで受信状況を確認してください。")


def build_scheduler():
    if not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", DAILY_TIME):
        raise ValueError("DAILY_NOTIFICATION_TIMEは18:00のように24時間表記で設定してください。")
    hour, minute = map(int, DAILY_TIME.split(":"))
    scheduler = BackgroundScheduler(timezone=JST)
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


@app.get("/")
def home():
    return "LINE Private Note Bot is running."


@app.post("/callback")
def callback():
    if not TOKEN or not SECRET:
        abort(503, "LINEの認証情報が未設定です。")
    signature = request.headers.get("X-Line-Signature", "")
    if not signature:
        abort(400, "署名がありません。")
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400, "署名が一致しません。")
    except ApiException as exc:
        app.logger.error("LINE reply error: HTTP %s", exc.status)
        abort(502, "返信に失敗しました。")
    except HTTPError:
        app.logger.error("LINE connection error")
        abort(502, "LINEへの接続に失敗しました。")
    except ValueError:
        abort(400, "Webhookの形式が不正です。")
    return "OK", 200


@handler.add(MessageEvent, message=TextMessageContent)
def echo(event):
    # 「質問」と送ると、通知時刻を待たずに選択式の質問を確認できる。
    if event.message.text.strip() == "質問":
        message = question_message()
    else:
        message = TextMessage(text=event.message.text)
    reply(event.reply_token, message)


@handler.add(PostbackEvent)
def answer(event):
    data = event.postback.data
    prefix = "answer:q1:"
    key = data[len(prefix):] if data.startswith(prefix) else ""
    if key in CHOICES:
        text = "回答ありがとうございます。\n" + CHOICES[key][1]
        text += "\n※この試作版では、回答をノートやデータベースに保存していません。"
    else:
        text = "選択肢を確認できませんでした。「質問」と送って、もう一度選んでください。"
    reply(event.reply_token, TextMessage(text=text))


def main():
    parser = argparse.ArgumentParser(description="私ノートの定期通知・選択回答テスト")
    parser.add_argument("--send-now", action="store_true", help="設定した相手へ質問を1回送信して終了")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    if args.send_now:
        try:
            send_question()
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        except ApiException as exc:
            raise SystemExit(f"送信に失敗しました。HTTP {exc.status}") from None
        except HTTPError:
            raise SystemExit("通信に失敗しました。再実行前にLINEの受信状況を確認してください。") from None
        print("送信要求がLINEに受け付けられました。スマートフォンで受信を確認してください。")
        return

    if not TOKEN or not SECRET:
        raise SystemExit("LINE_CHANNEL_ACCESS_TOKENとLINE_CHANNEL_SECRETを.envに設定してください。")
    if SCHEDULE_SETTING not in {"true", "false"}:
        raise SystemExit("ENABLE_SCHEDULEはtrueまたはfalseにしてください。")

    scheduler = None
    if SCHEDULE_SETTING == "true":
        try:
            validate_push_settings()
            scheduler = build_scheduler()
        except ValueError as exc:
            raise SystemExit(str(exc)) from None
        scheduler.start()
        app.logger.info("定期通知: 毎日 %s（日本時間）", DAILY_TIME)
    try:
        # この起動方法で1プロセスだけ実行する。二重起動すると定期通知も重複する。
        app.run(host="127.0.0.1", port=5001, debug=False, use_reloader=False)
    finally:
        if scheduler is not None and scheduler.running:
            scheduler.shutdown(wait=True)


if __name__ == "__main__":
    main()
