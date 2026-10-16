"""暗号化済み回答の保存。平文の選択肢は受け取らない。"""

from __future__ import annotations

from sqlalchemy import func

from app.crypto_contract import (
    ANSWER_ITEM_STATES,
    CRYPTO_VERSION,
    DOCUMENT_AAD,
    DOCUMENT_INFO,
    SCHEMA_VERSION,
)
from app.extensions import db
from app.models import PrivateAnswerDocument, Question, QuestionCategory, UserDataKey, utcnow
from app.services.support import (
    audit,
    b64decode,
    b64encode,
    ciphertext_limit,
    commit_or_conflict,
    expect_fields,
    fail,
    iso,
    load_login,
)

FIELDS = {"expected_revision", "data_key_id", "nonce", "ciphertext", "crypto_version", "schema_version"}


def contract() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "crypto_version": CRYPTO_VERSION,
        "document_info": DOCUMENT_INFO,
        "document_aad": DOCUMENT_AAD,
        "item_states": list(ANSWER_ITEM_STATES),
        "unanswered": "itemsに存在しないIDは未回答です。",
        "intentional": "undecided（未定）とdeclined（回答しない）は、未回答とは別の意思です。",
    }


def question_index() -> dict:
    rows = db.session.query(Question).filter_by(is_active=True).order_by(Question.sort_order.asc()).all()
    categories = (
        db.session.query(QuestionCategory)
        .filter_by(is_active=True)
        .order_by(QuestionCategory.sort_order.asc())
        .all()
    )
    names = {row.id: row.name for row in categories}
    return {
        "categories": [{"id": row.id, "name": row.name} for row in categories],
        "questions": [{
            "id": row.id,
            "question_key": row.question_key,
            "version": row.version,
            "category_id": row.category_id,
            "category_name": names.get(row.category_id),
            "answer_type": row.answer_type,
            "text": row.text,
        } for row in rows],
    }


def _latest(user_id: str) -> PrivateAnswerDocument | None:
    return (
        db.session.query(PrivateAnswerDocument)
        .filter_by(user_id=user_id)
        .order_by(PrivateAnswerDocument.revision.desc())
        .first()
    )


def _view(row: PrivateAnswerDocument) -> dict:
    return {
        "id": row.id,
        "revision": row.revision,
        "data_key_id": row.data_key_id,
        "nonce": b64encode(row.nonce),
        "ciphertext": b64encode(row.ciphertext),
        "crypto_version": row.crypto_version,
        "schema_version": row.schema_version,
        "created_at": iso(row.created_at),
    }


def current_answer() -> dict:
    user, _session, _passkey = load_login()
    row = _latest(user.id)
    if row is None:
        return {"revision": 0, "document": None}
    return {"revision": row.revision, "document": _view(row)}


def save_answer(body: dict) -> dict:
    user, _session, _passkey = load_login()
    expect_fields(body, FIELDS)
    if body.get("crypto_version") != CRYPTO_VERSION or body.get("schema_version") != SCHEMA_VERSION:
        fail("unsupported_crypto", 400, "未対応の暗号方式です。", actor_id=user.id)
    expected = body.get("expected_revision")
    if not isinstance(expected, int) or isinstance(expected, bool) or expected < 0:
        fail("invalid_revision", 400, "revisionの指定が正しくありません。", actor_id=user.id)
    data_key = db.session.get(UserDataKey, body.get("data_key_id"))
    if data_key is None or data_key.user_id != user.id or data_key.retired_at is not None:
        fail("key_unavailable", 409, "保管用の鍵が見つかりません。", actor_id=user.id)
    nonce = b64decode(body.get("nonce"), exact=12)
    ciphertext = b64decode(body.get("ciphertext"), minimum=16, maximum=ciphertext_limit())
    latest = db.session.query(func.max(PrivateAnswerDocument.revision)).filter_by(user_id=user.id).scalar()
    current = int(latest or 0)
    if expected != current:
        fail("revision_conflict", 409, "別の更新が入ったため、保存できませんでした。",
             action="answer_save", target_type="answer", actor_id=user.id)
    document = PrivateAnswerDocument(
        user_id=user.id,
        data_key_id=data_key.id,
        revision=current + 1,
        ciphertext=ciphertext,
        nonce=nonce,
        crypto_version=CRYPTO_VERSION,
        schema_version=SCHEMA_VERSION,
        created_at=utcnow(),
    )
    db.session.add(document)
    audit("answer_saved", "answer", str(current + 1), "success", actor_id=user.id)
    commit_or_conflict("別の更新が入ったため、保存できませんでした。", code="revision_conflict")
    return {"revision": document.revision, "document": _view(document)}
