"""認証の入口。

パスキーは未実装。ログインしたことにする処理は置かない。
"""

from flask import Blueprint, render_template

bp = Blueprint("auth", __name__)


@bp.get("/auth", strict_slashes=False)
def index():
    return render_template("auth/index.html")
