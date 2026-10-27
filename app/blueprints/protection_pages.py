"""森下担当の画面。QRの画像生成と回答入力フォームは置かない。"""

from flask import Blueprint, render_template

bp = Blueprint("protection_pages", __name__)


@bp.get("/family", strict_slashes=False)
def family():
    return render_template("family/index.html")


@bp.get("/family/invite", strict_slashes=False)
def family_invite():
    return render_template("family/invite.html")


@bp.get("/transfer", strict_slashes=False)
def transfer():
    return render_template("transfer/index.html")


@bp.get("/view/<share_id>", strict_slashes=False)
def view_qr(share_id):
    return render_template("view/index.html", share_id=share_id)
