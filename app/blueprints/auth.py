"""ログイン・新規登録と、ログイン後のアカウント管理画面。"""

from urllib.parse import unquote, urlsplit, urlunsplit

from flask import Blueprint, redirect, render_template, request, url_for

from app.services.auth_service import session_state

bp = Blueprint("auth", __name__)


def safe_return_path(value):
    fallback = url_for("main.index")
    if not isinstance(value, str):
        return fallback
    for candidate in (value, unquote(value)):
        if (not candidate.startswith("/") or candidate.startswith("//")
                or "\\" in candidate or any(ord(char) < 32 or ord(char) == 127 for char in candidate)):
            return fallback
    try:
        target = urlsplit(value)
        decoded = urlsplit(unquote(value))
    except ValueError:
        return fallback
    if (target.scheme or target.netloc or decoded.scheme or decoded.netloc
            or decoded.path.rstrip("/") == url_for("auth.index")):
        return fallback
    return urlunsplit(("", "", target.path, target.query, ""))


@bp.get("/auth", strict_slashes=False)
def index():
    return render_template("auth/index.html", return_url=safe_return_path(request.args.get("next")))


@bp.get("/account", strict_slashes=False)
def account():
    if not session_state()["authenticated"]:
        return redirect(url_for("auth.index", next=request.full_path if request.query_string else request.path))
    return render_template("auth/account.html")
