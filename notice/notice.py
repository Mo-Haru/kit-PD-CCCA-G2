"""LINEの接続確認用Echo Bot。個人情報はテストに使わない。"""
import os

from dotenv import load_dotenv
from flask import Flask, abort, request
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient, ApiException, Configuration, MessagingApi,
    ReplyMessageRequest, TextMessage,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from urllib3.exceptions import HTTPError

load_dotenv()
TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "").strip()
SECRET = os.getenv("LINE_CHANNEL_SECRET", "").strip()
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024
handler = WebhookHandler(SECRET)
configuration = Configuration(access_token=TOKEN)


@app.get("/")
def home():
    return "LINE Echo Bot is running."


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
    with ApiClient(configuration) as client:
        MessagingApi(client).reply_message_with_http_info(
            ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=event.message.text)],
            ),
            _request_timeout=10,
        )


if __name__ == "__main__":
    if not TOKEN or not SECRET:
        raise SystemExit("LINE_CHANNEL_ACCESS_TOKENとLINE_CHANNEL_SECRETを.envに設定してください。")
    app.run(host="127.0.0.1", port=5001, debug=False)
