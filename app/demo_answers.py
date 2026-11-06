"""質問の登録。回答は既存の暗号化保存APIで扱い、ここでは作らない。"""

import json
from pathlib import Path
from uuid import UUID, uuid5

import click
from flask import current_app
from flask.cli import with_appcontext

from app.extensions import db
from app.models import GlossaryTerm, Question, QuestionCategory, QuestionGlossaryTerm, QuestionOption


def load_catalog(filename):
    source = Path(current_app.root_path) / "data" / filename
    catalog = json.loads(source.read_text(encoding="utf-8"))
    for number, question in enumerate(catalog["questions"], start=1):
        question["number"] = number
    return catalog


def demo_catalog():
    return load_catalog("answer-demo.json")


def seed_catalog(catalog):
    """Add a fixed catalog once; never replace existing questions or answers."""
    added = 0

    def ensure(model, identity, fields):
        nonlocal added
        existing = db.session.get(model, identity)
        if existing is not None:
            if any(getattr(existing, key) != value for key, value in fields.items()):
                raise ValueError("質問のIDと既存データが一致しません。既存データは変更していません。")
            return
        db.session.add(model(id=identity, **fields))
        added += 1

    # Sorting after existing entries keeps their relative order unchanged.
    category_order = db.session.query(db.func.max(QuestionCategory.sort_order)).scalar() or 0
    question_order = db.session.query(db.func.max(Question.sort_order)).scalar() or 0
    try:
        terms = {}
        for entry in catalog.get("glossary", []):
            identity = str(uuid5(UUID(int=0), f"glossary:{entry['term']}"))
            ensure(GlossaryTerm, identity, {"term": entry["term"], "description": entry["description"]})
            terms[entry["term"]] = identity
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
            for term in question.get("glossary", []):
                link = (question["id"], terms[term])
                if db.session.get(QuestionGlossaryTerm, link) is None:
                    db.session.add(QuestionGlossaryTerm(question_id=link[0], glossary_term_id=link[1]))
                    added += 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return added


def seed_demo_questions():
    return seed_catalog(demo_catalog())


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


def acp_catalog():
    return load_catalog("acp-questions.json")


def seed_acp_questions():
    return seed_catalog(acp_catalog())


@click.command("seed-questions")
@with_appcontext
def seed_questions_command():
    """回答画面の質問（人生会議の質問と用語の説明）を追加。既存の質問・回答は変更しない。"""
    try:
        added = seed_acp_questions()
    except ValueError as error:
        raise click.ClickException(str(error)) from error
    count = len(acp_catalog()["questions"])
    click.echo(f"回答画面の質問{count}問を確認しました（追加した質問・章・選択肢・用語: {added}件）。回答は変更していません。")
