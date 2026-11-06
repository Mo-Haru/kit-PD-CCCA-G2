"""回答の入力（/answers）、回答の進み具合（/note）、テスト用の回答画面（/answers/demo）。

回答は既存の暗号化保存APIでブラウザ内で暗号化して保存する。
HTML画面のルートで回答本文を受け取らず、POST は受け付けない。
"""

from flask import Blueprint, render_template

from app.demo_answers import acp_catalog, demo_catalog

bp = Blueprint("answers", __name__)


@bp.get("/answers", strict_slashes=False)
def index():
    """人生会議（ACP）の質問に答える画面（今牧担当の画面を統合）。"""
    return render_template("answers/index.html", catalog=acp_catalog())


@bp.get("/note", strict_slashes=False)
def note():
    """わたしノート：回答の進み具合と、質問ごとの回答（沖担当の画面を統合）。"""
    return render_template("answers/note.html", catalog=acp_catalog())


@bp.get("/answers/demo", strict_slashes=False)
def demo():
    """ダミー質問を既存の暗号化保存・共有APIで試す。"""
    return render_template("answers/demo.html", catalog=demo_catalog())
