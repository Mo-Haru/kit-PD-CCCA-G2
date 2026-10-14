"""拡張機能のインスタンス。

db はここで一度だけ作る。初期化は create_app 内の db.init_app で行う。
"""

from typing import TYPE_CHECKING, Any

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    # ORMが実行時に生成するキーワード引数コンストラクタを型検査にも伝える。
    if TYPE_CHECKING:
        def __init__(self, **kwargs: Any) -> None: ...

    metadata = MetaData(naming_convention={
        "ix": "ix_%(table_name)s_%(column_0_name)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    })


db = SQLAlchemy(model_class=Base)
