from datetime import datetime, time, timedelta, timezone

import pytest
from sqlalchemy import inspect, text, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import models
from app.extensions import db
from app.models import (
    AuditLog,
    AuthSession,
    ExternalAccount,
    GlossaryTerm,
    NotificationDelivery,
    NotificationSetting,
    PasskeyCredential,
    PrivateAnswerDocument,
    Question,
    QuestionCategory,
    QuestionGlossaryTerm,
    QuestionOption,
    RecoveryKeyEnvelope,
    SharedSnapshot,
    ShareRecipient,
    User,
    UserDataKey,
    UserKeyEnvelope,
    WebAuthnChallenge,
    utcnow,
)


@pytest.fixture
def model_session(app):
    with app.app_context():
        db.create_all()
        yield db.session
        db.session.remove()
        db.engine.dispose()


@pytest.fixture
def encrypted_rows(model_session):
    users = [User(), User()]
    model_session.add_all(users)
    model_session.flush()
    passkeys = [
        PasskeyCredential(user_id=user.id, credential_id=bytes([i]), public_key=b"public-key")
        for i, user in enumerate(users)
    ]
    keys = [UserDataKey(user_id=user.id, key_version=1) for user in users]
    model_session.add_all(passkeys + keys)
    model_session.flush()
    documents = [
        PrivateAnswerDocument(
            user_id=user.id, data_key_id=key.id, revision=1,
            ciphertext=b"c" * 32, nonce=b"n" * 12,
        )
        for user, key in zip(users, keys)
    ]
    model_session.add_all(documents)
    model_session.flush()
    snapshot = SharedSnapshot(
        owner_user_id=users[0].id, source_document_id=documents[0].id,
        owner_data_key_id=keys[0].id, ciphertext=b"c" * 32, nonce=b"s" * 12,
        owner_wrapped_share_key=b"w" * 48, owner_wrap_nonce=b"o" * 12,
    )
    model_session.add(snapshot)
    model_session.commit()
    return users, passkeys, keys, documents, snapshot


def test_init_db_registers_all_19_models(app):
    result = app.test_cli_runner().invoke(args=["init-db"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        exported = [getattr(models, name) for name in models.__all__ if name not in {"db", "utcnow"}]
        names = {model.__tablename__ for model in exported}
        original = {
            "users", "passkey_credentials", "webauthn_challenges", "auth_sessions",
            "user_data_keys", "user_key_envelopes", "private_answer_documents",
            "shared_snapshots", "share_recipients", "question_categories", "questions",
            "question_options", "glossary_terms", "question_glossary_terms", "audit_logs",
            "recovery_key_envelopes", "external_accounts", "notification_settings",
            "notification_deliveries",
        }
        assert original <= names
        assert len(exported) == len(names)
        assert set(inspect(db.engine).get_table_names()) == names
        assert models.db is db
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar_one() == 1
        for table in db.metadata.tables.values():
            assert all(constraint.name for constraint in table.constraints)


def test_user_defaults_and_utc_round_trip(model_session):
    local = datetime(2026, 10, 14, 12, tzinfo=timezone(timedelta(hours=9)))
    user = User(created_at=local)
    model_session.add(user)
    model_session.commit()
    model_session.refresh(user)
    assert len(user.id) == 36
    assert len(user.webauthn_user_handle) == 32
    assert user.status == "pending"
    assert user.created_at == local.astimezone(timezone.utc)
    assert user.created_at.tzinfo is timezone.utc
    assert user.updated_at.tzinfo is timezone.utc


def test_naive_datetime_is_rejected(model_session):
    model_session.add(User(created_at=datetime(2026, 10, 14)))
    with pytest.raises(StatementError, match="timezone-aware datetime required"):
        model_session.flush()
    model_session.rollback()


@pytest.mark.parametrize("case", ["envelope_key", "envelope_passkey", "document", "recovery", "session", "snapshot_document", "snapshot_key"])
def test_composite_foreign_keys_reject_other_owner(model_session, encrypted_rows, case):
    users, passkeys, keys, documents, _snapshot = encrypted_rows
    envelope_fields = {
        "user_id": users[0].id, "prf_input": b"p" * 32, "kdf_salt": b"k" * 32,
        "wrapped_key": b"w" * 48, "nonce": b"e" * 12,
    }
    snapshot_fields = {
        "owner_user_id": users[0].id, "ciphertext": b"c" * 32, "nonce": b"s" * 12,
        "owner_wrapped_share_key": b"w" * 48, "owner_wrap_nonce": b"o" * 12,
    }
    invalid = {
        "envelope_key": UserKeyEnvelope(**envelope_fields, data_key_id=keys[1].id, passkey_id=passkeys[0].id),
        "envelope_passkey": UserKeyEnvelope(**envelope_fields, data_key_id=keys[0].id, passkey_id=passkeys[1].id),
        "document": PrivateAnswerDocument(
            user_id=users[0].id, data_key_id=keys[1].id, revision=2,
            ciphertext=b"c" * 32, nonce=b"d" * 12,
        ),
        "recovery": RecoveryKeyEnvelope(
            user_id=users[0].id, data_key_id=keys[1].id, kdf_salt=b"k" * 32,
            wrapped_key=b"w" * 48, nonce=b"r" * 12,
        ),
        "session": AuthSession(
            user_id=users[0].id, passkey_id=passkeys[1].id, token_hash=b"t" * 32,
            expires_at=utcnow() + timedelta(hours=1),
        ),
        "snapshot_document": SharedSnapshot(
            **snapshot_fields, source_document_id=documents[1].id, owner_data_key_id=keys[0].id,
        ),
        "snapshot_key": SharedSnapshot(
            **snapshot_fields, source_document_id=documents[0].id, owner_data_key_id=keys[1].id,
        ),
    }[case]
    model_session.add(invalid)
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        model_session.flush()
    model_session.rollback()


@pytest.mark.parametrize("revision, nonce", [(1, b"x" * 12), (2, b"n" * 12)])
def test_document_revision_and_nonce_are_unique(model_session, encrypted_rows, revision, nonce):
    users, _passkeys, keys, _documents, _snapshot = encrypted_rows
    model_session.add(PrivateAnswerDocument(
        user_id=users[0].id, data_key_id=keys[0].id, revision=revision,
        ciphertext=b"c" * 32, nonce=nonce,
    ))
    with pytest.raises(IntegrityError, match="UNIQUE constraint failed"):
        model_session.flush()
    model_session.rollback()


@pytest.mark.parametrize("field, value", [("nonce", b"short"), ("ciphertext", b"short"), ("revision", 0)])
def test_document_checks(model_session, encrypted_rows, field, value):
    users, _passkeys, keys, _documents, _snapshot = encrypted_rows
    fields = {
        "user_id": users[0].id, "data_key_id": keys[0].id, "revision": 2,
        "ciphertext": b"c" * 32, "nonce": b"d" * 12,
    }
    fields[field] = value
    model_session.add(PrivateAnswerDocument(**fields))
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        model_session.flush()
    model_session.rollback()


def test_user_delete_does_not_cascade(model_session, encrypted_rows):
    users, _passkeys, _keys, _documents, _snapshot = encrypted_rows
    assert len(users[0].passkeys) == 1
    model_session.delete(users[0])
    with pytest.raises(IntegrityError, match="FOREIGN KEY constraint failed"):
        model_session.flush()
    model_session.rollback()
    assert model_session.get(User, users[0].id) is not None


def test_challenge_can_only_be_consumed_once(model_session):
    now = utcnow()
    challenge = WebAuthnChallenge(
        purpose="login", flow_binding_hash=b"b" * 32,
        created_at=now, expires_at=now + timedelta(minutes=5),
    )
    model_session.add(challenge)
    model_session.commit()
    statement = update(WebAuthnChallenge).where(
        WebAuthnChallenge.id == challenge.id,
        WebAuthnChallenge.consumed_at.is_(None),
        WebAuthnChallenge.expires_at > now,
    ).values(consumed_at=now)
    assert model_session.execute(statement).rowcount == 1
    assert model_session.execute(statement).rowcount == 0
    model_session.commit()


def test_snapshot_availability_and_optimistic_lock(model_session, encrypted_rows):
    *_rows, snapshot = encrypted_rows
    now = utcnow()
    assert len(snapshot.id) == 43
    assert not snapshot.is_available(now)
    snapshot.is_enabled = True
    snapshot.expires_at = now + timedelta(hours=1)
    model_session.commit()
    assert snapshot.is_available(now)
    assert not snapshot.is_available(snapshot.expires_at)
    snapshot.revoked_at = now
    assert not snapshot.is_available(now)
    model_session.rollback()

    with Session(db.engine) as first, Session(db.engine) as second:
        first_copy = first.get(SharedSnapshot, snapshot.id)
        second_copy = second.get(SharedSnapshot, snapshot.id)
        assert first_copy is not None and second_copy is not None
        first_copy.is_enabled = False
        first.commit()
        second_copy.expires_at = now + timedelta(hours=2)
        with pytest.raises(StaleDataError):
            second.commit()


def test_question_relationships_and_optional_models(model_session, encrypted_rows):
    users, passkeys, keys, _documents, snapshot = encrypted_rows
    category = QuestionCategory(name="価値観")
    term = GlossaryTerm(term="人生会議", description="説明")
    model_session.add_all([category, term])
    model_session.flush()
    question = Question(
        question_key="values", category_id=category.id, text="質問",
        answer_type="single_choice",
        options=[QuestionOption(text="後", sort_order=2), QuestionOption(text="先", sort_order=1)],
    )
    account = ExternalAccount(user_id=users[0].id, provider="line", provider_user_id="external-id")
    model_session.add_all([question, account])
    model_session.flush()
    setting = NotificationSetting(external_account_id=account.id, frequency="weekly", weekday=0, local_time=time(9))
    model_session.add(setting)
    model_session.flush()
    model_session.add_all([
        QuestionGlossaryTerm(question_id=question.id, glossary_term_id=term.id),
        NotificationDelivery(setting_id=setting.id, scheduled_at=utcnow(), deduplication_key="weekly-reminder"),
        ShareRecipient(snapshot_id=snapshot.id, recipient_user_id=users[1].id),
        AuditLog(actor_user_id=users[0].id, action="share_created", target_type="snapshot", target_id=snapshot.id, result="success"),
        UserKeyEnvelope(
            user_id=users[0].id, data_key_id=keys[0].id, passkey_id=passkeys[0].id,
            prf_input=b"p" * 32, kdf_salt=b"k" * 32, wrapped_key=b"w" * 48, nonce=b"e" * 12,
        ),
        RecoveryKeyEnvelope(user_id=users[0].id, data_key_id=keys[0].id, kdf_salt=b"k" * 32, wrapped_key=b"w" * 48, nonce=b"r" * 12),
        AuthSession(user_id=users[0].id, passkey_id=passkeys[0].id, token_hash=b"t" * 32, expires_at=utcnow() + timedelta(hours=1)),
    ])
    model_session.commit()
    model_session.expire_all()
    assert [option.text for option in question.options] == ["先", "後"]
    assert setting.timezone == "Asia/Tokyo"
    assert setting.local_time == time(9)
    assert not setting.enabled


@pytest.mark.parametrize("frequency", ["weekly", "monthly"])
def test_notification_requires_weekday_or_month_day(model_session, encrypted_rows, frequency):
    users, *_rows = encrypted_rows
    account = ExternalAccount(user_id=users[0].id, provider="line", provider_user_id="external-id")
    model_session.add(account)
    model_session.flush()
    model_session.add(NotificationSetting(external_account_id=account.id, frequency=frequency, local_time=time(9)))
    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        model_session.flush()
    model_session.rollback()
