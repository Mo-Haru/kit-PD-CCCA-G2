"""共有設定の入口。

暗号化済みの共有リンクとそのQRコードを作成・停止する。QR画像は端末の中だけで描く。
回答の更新を共有先へ自動反映する処理は置かない。
"""

from flask import Blueprint, render_template

bp = Blueprint("sharing", __name__)


@bp.get("/sharing", strict_slashes=False)
def index():
    return render_template("sharing/index.html")
