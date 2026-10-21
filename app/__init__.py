"""Flask アプリケーションの組み立て。

機能の処理は blueprints に置く。このファイルでは作成と登録だけを行う。
"""

import mimetypes
from pathlib import Path

import click
from flask import Flask, jsonify, render_template, request
from flask.cli import with_appcontext

from app.extensions import db

mimetypes.add_type("text/javascript", ".mjs")

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
)
SENSITIVE_PREFIXES = ("/auth", "/family", "/transfer", "/view", "/api/")
CSRF_EXEMPT = {
    "protection_api.register_options",
    "protection_api.login_options",
    "protection_api.qr_start",
    "protection_api.transfer_bind",
}


def create_app(config_object=None):
    app = Flask(__name__)
    if config_object is None:
        from config import get_config

        config_object = get_config()
    app.config.from_object(config_object)

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    db.init_app(app)
    register_blueprints(app)
    register_commands(app)
    register_error_handlers(app)
    register_protection(app)

    # モデル定義をメタデータへ載せる。テーブル作成は init-db だけが行う。
    from app import models  # noqa: F401

    return app


def register_blueprints(app):
    from app.blueprints.answers import bp as answers_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.main import bp as main_bp
    from app.blueprints.protection_api import bp as protection_api_bp
    from app.blueprints.protection_pages import bp as protection_pages_bp
    from app.blueprints.sharing import bp as sharing_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(answers_bp)
    app.register_blueprint(sharing_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(protection_pages_bp)
    app.register_blueprint(protection_api_bp)


def register_commands(app):
    app.cli.add_command(init_db_command)


def register_error_handlers(app):
    from app.services.support import ApiError

    @app.errorhandler(404)
    def not_found(_error):
        if request.path.startswith("/api/"):
            return jsonify(error="not_found", message="見つかりません。"), 404
        return render_template("404.html"), 404

    @app.errorhandler(ApiError)
    def api_error(error):
        db.session.rollback()
        body = {"error": error.code, "message": error.message}
        body.update(error.details)
        return jsonify(body), error.status


def register_protection(app):
    from app.services.support import plant_csrf, tokens_match

    @app.before_request
    def check_csrf():
        if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
            return None
        if not request.path.startswith("/api/"):
            return None
        if request.endpoint in CSRF_EXEMPT:
            return None
        if tokens_match(request.cookies.get("wn_csrf"), request.headers.get("X-CSRF-Token")):
            return None
        return jsonify(error="csrf", message="ページを開き直してから操作してください。"), 403

    @app.after_request
    def protect_response(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.path.startswith(SENSITIVE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Content-Security-Policy"] = CSP
            return plant_csrf(response)
        return response


@click.command("init-db")
@with_appcontext
def init_db_command():
    """モデルのテーブルを作る。既存のテーブルとデータは削除しない。"""
    db.create_all()
    from app.schema_upgrade import apply_schema_upgrades

    apply_schema_upgrades()
    click.echo("データベースを初期化しました。既存のテーブルとデータは削除していません。")
