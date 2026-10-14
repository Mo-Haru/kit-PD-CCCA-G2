"""回答の入力・保存・閲覧の入口。

保存処理は未実装。回答本文をサーバーが読める形で受け取らない。
ブラウザ側の暗号化が決まるまで、POST は受け付けない。
"""

from flask import Blueprint, render_template

bp = Blueprint("answers", __name__)


@bp.get("/answers", strict_slashes=False)
def index():
    return render_template("answers/index.html")
