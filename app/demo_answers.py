"""Development questions only. Answers use the existing encrypted document API."""

import json
from pathlib import Path
from uuid import UUID, uuid5

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.models import Question, QuestionCategory, QuestionOption


def demo_catalog():
    source = Path(current_app.root_path) / "data" / "answer-demo.json"
    catalog = json.loads(source.read_text(encoding="utf-8"))
    for number, question in enumerate(catalog["questions"], start=1):
        question["number"] = number
    return catalog


def seed_demo_questions():
    """Add the fixed fixture once; never replace existing questions or answers."""
    catalog = demo_catalog()
    added = 0

    def ensure(model, identity, fields):
        nonlocal added
        existing = db.session.get(model, identity)
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in fields.items()):
                raise ValueError("テスト質問のIDと既存データが一致しません。既存データは変更していません。")
            return
        db.session.add(model(id=identity, **fields))
        added += 1

    # Sorting after existing entries keeps their relative order unchanged.
    category_order = db.session.query(db.func.max(QuestionCategory.sort_order)).scalar() or 0
    question_order = db.session.query(db.func.max(Question.sort_order)).scalar() or 0
    try:
        for offset, category in enumerate(catalog["categories"], start=1):
            fields = {"name": category["title"], "description": category["description"], "is_active": True}
            existing = db.session.get(QuestionCategory, category["id"])
            fields["sort_order"] = existing.sort_order if existing else category_order + offset
            ensure(QuestionCategory, category["id"], fields)
        db.session.flush()
        for offset, question in enumerate(catalog["questions"], start=1):
            kind = question["input_type"]
            fields = {
                "question_key": question["question_key"], "version": 1,
                "category_id": question["category_id"], "text": question["text"],
                "answer_type": kind if kind in {"single_choice", "multiple_choice"}
                else "single_choice" if kind == "select" else "text",
                "is_active": True,
            }
            existing = db.session.get(Question, question["id"])
            fields["sort_order"] = existing.sort_order if existing else question_order + offset
            ensure(Question, question["id"], fields)
            db.session.flush()
            for order, option in enumerate(question.get("options", [])):
                identity = str(uuid5(UUID(question["id"]), option["value"]))
                ensure(QuestionOption, identity, {
                    "question_id": question["id"], "text": option["text"], "sort_order": order,
                })
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return added


@click.command("seed-answer-demo")
@with_appcontext
def seed_answer_demo_command():
    """開発用の質問6問を追加。回答・アカウントは作成しない。"""
    if not current_app.config.get("DEMO_QUESTIONS_ENABLED", False):
        raise click.ClickException("このコマンドは開発・テスト環境専用です。")
    try:
        added = seed_demo_questions()
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    click.echo(f"テスト質問6問を確認しました（追加した質問・章・選択肢: {added}件）。回答は変更していません。")
