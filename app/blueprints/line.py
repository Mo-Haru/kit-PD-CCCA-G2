"""LINEの受信口（Webhook）。署名を確かめてから処理し、回答は保存しない。"""

from flask import Blueprint, abort, current_app, request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import ApiException
from urllib3.exceptions import HTTPError

from app.services import line

bp = Blueprint("line", __name__)


@bp.post("/line/callback")
def callback():
    if not line.configured():
        abort(503, "LINEの認証情報が未設定です。")
    signature = request.headers.get("X-Line-Signature", "")
    if not signature:
        abort(400, "署名がありません。")
    body = request.get_data(as_text=True)
    try:
        line.webhook_handler().handle(body, signature)
    except InvalidSignatureError:
        abort(400, "署名が一致しません。")
    except ApiException as exc:
        current_app.logger.error("LINE reply error: HTTP %s", exc.status)
        abort(502, "返信に失敗しました。")
    except HTTPError:
        current_app.logger.error("LINE connection error")
        abort(502, "LINEへの接続に失敗しました。")
    except ValueError:
        abort(400, "Webhookの形式が不正です。")
    return "OK", 200
