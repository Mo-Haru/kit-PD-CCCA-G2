"""既存データを消さないスキーマ手順。

create_allは未作成テーブルの追加にだけ使う。既存テーブルの列変更が必要になったら、
新しい手順関数で明示的なALTERを書き、この一覧へ足す。起動時にテーブルを削除しない。
"""

from __future__ import annotations

from app.extensions import db
from app.models.protection import PROTECTION_MODELS, SchemaMigration


def _create_protection_tables() -> None:
    tables = [model.__table__ for model in PROTECTION_MODELS]
    db.metadata.create_all(bind=db.engine, tables=tables, checkfirst=True)


STEPS = (("20261015_protection_tables", _create_protection_tables),)


def apply_schema_upgrades() -> list[str]:
    """未適用の手順だけを実行し、適用した版名を返す。既存行は削除しない。"""
    SchemaMigration.__table__.create(bind=db.engine, checkfirst=True)
    applied = {row.version for row in db.session.query(SchemaMigration.version)}
    fresh: list[str] = []
    for version, step in STEPS:
        if version in applied:
            continue
        step()
        db.session.add(SchemaMigration(version=version))
        db.session.commit()
        fresh.append(version)
    return fresh
