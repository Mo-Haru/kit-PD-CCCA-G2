"""開発サーバーの入口。

機能のコードは書かない。起動コマンドは README を参照する。
"""

from app import create_app

app = create_app()


if __name__ == "__main__":
    raise SystemExit(
        "python run.py では起動しません。README の flask コマンドを使ってください。"
    )
