"""Flask アプリケーションの組み立て。

機能の処理は blueprints に置く。このファイルでは作成と登録だけを行う。
"""

from pathlib import Path

import click
from flask import Flask, render_template
from flask.cli import with_appcontext

from app.extensions import db


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

    # モデル定義をメタデータへ載せる。テーブル作成は init-db だけが行う。
    from app import models  # noqa: F401

    return app


def register_blueprints(app):
    from app.blueprints.answers import bp as answers_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.main import bp as main_bp
    from app.blueprints.sharing import bp as sharing_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(answers_bp)
    app.register_blueprint(sharing_bp)
    app.register_blueprint(auth_bp)


def register_commands(app):
    app.cli.add_command(init_db_command)


def register_error_handlers(app):
    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404


@click.command("init-db")
@with_appcontext
def init_db_command():
    """モデルのテーブルを作る。既存のテーブルとデータは削除しない。"""
    db.create_all()
    click.echo("データベースを初期化しました。既存のテーブルとデータは削除していません。")
