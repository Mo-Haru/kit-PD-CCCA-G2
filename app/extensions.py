"""拡張機能のインスタンス。

db はここで一度だけ作る。初期化は create_app 内の db.init_app で行う。
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
