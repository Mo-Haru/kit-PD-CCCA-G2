"""E2E確認専用の起動口。回数制限だけを外した開発設定で起動する。

テストのブラウザはすべて同じ接続元（127.0.0.1）になり、登録やログインの回数制限に達するため。
本番や通常の開発では使わない。
"""

from app import create_app
from config import DevelopmentConfig


class E2EConfig(DevelopmentConfig):
    RATELIMIT_ENABLED = False


app = create_app(E2EConfig)
