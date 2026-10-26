"""回答の入力・保存・閲覧の入口。

本実装の /answers は未実装。/answers/demo は既存の暗号化保存APIを使う。
HTML画面のルートで回答本文を受け取らず、POST は受け付けない。
"""

from flask import Blueprint, render_template

from app.demo_answers import demo_catalog

bp = Blueprint("answers", __name__)


@bp.get("/answers", strict_slashes=False)
def index():
    return render_template("answers/index.html")


@bp.get("/answers/demo", strict_slashes=False)
def demo():
    """ダミー質問を既存の暗号化保存・共有APIで試す。"""
    return render_template("answers/demo.html", catalog=demo_catalog())
