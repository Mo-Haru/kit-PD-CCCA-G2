"""トップページ、起動確認、既存の Hello 応答。"""

from flask import Blueprint, jsonify, render_template
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db

bp = Blueprint("main", __name__)


@bp.get("/")
def index():
    return render_template("index.html")


@bp.get("/hello", strict_slashes=False)
def hello():
    """以前の app.py が返していた確認用の応答。"""
    return "Hello, World!"


@bp.get("/health")
def health():
    try:
        db.session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        db.session.rollback()
        return jsonify(status="error", database="error"), 503
    return jsonify(status="ok", database="ok")
