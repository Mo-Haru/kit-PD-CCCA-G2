"""確認コード・確認リンクの承認は、同時の誤入力・再発行・取り消しでも状態が食い違わない。"""

import threading

import pytest

from app.extensions import db
from app.models import FamilyConnection, FamilyInvitation
from config import TestingConfig
from tests.test_protection import post, register

ROUNDS = 6


def file_app(tmp_path):
    from app import create_app

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "race.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {"connect_args": {"timeout": 15}}
        RATELIMIT_ENABLED = False

    application = create_app(FileConfig)
    with application.app_context():
        db.create_all()
    return application


def session_client(app, source):
    client = app.test_client()
    for name in ("wn_session", "wn_csrf"):
        client.set_cookie(name, source.get_cookie(name).value)
    return client


def run_together(*targets):
    barrier = threading.Barrier(len(targets))
    results = [None] * len(targets)

    def worker(index):
        barrier.wait()
        response = targets[index]()
        results[index] = (response.status_code, (response.get_json() or {}).get("error"))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(len(targets))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    return results


def requested(app, owner, name):
    recipient = app.test_client()
    register(recipient, name)
    invited = post(owner, "/api/v1/family/invitations", {"kind": "open"}).get_json()
    asked = post(recipient, "/api/v1/family/invitations/request", {"token": invited["token"]})
    assert asked.status_code == 200, asked.get_json()
    return recipient, invited["invitation"]["id"], asked.get_json()["confirmation"]


def state(app, invitation_id):
    with app.app_context():
        row = db.session.get(FamilyInvitation, invitation_id)
        connections = db.session.query(FamilyConnection).filter_by(invitation_id=invitation_id).count()
        return row.status, row.confirm_failures, connections


def test_parallel_wrong_codes_count_every_attempt(tmp_path):
    app = file_app(tmp_path)
    owner = app.test_client()
    register(owner, "招待する人")
    _recipient, invitation_id, confirmation = requested(app, owner, "申請する人")
    wrong = "AAAA-AAAA" if confirmation["code"] != "AAAA-AAAA" else "BBBB-BBBB"
    clients = [session_client(app, owner) for _ in range(8)]
    results = run_together(*[
        (lambda client=client: post(client, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": wrong}))
        for client in clients
    ])
    errors = [error for _status, error in results]
    # 数えられる誤りは5回まで。5回目で申請が無効になり、それ以降の要求は無効になったことだけを返す。
    assert errors.count("confirmation_mismatch") == 4, results
    assert errors.count("confirmation_locked") == 4, results
    assert state(app, invitation_id) == ("cancelled", 5, 0)
    with app.app_context():
        from app.models import AuditLog
        actions = [entry.action for entry in db.session.query(AuditLog).all()]
    assert actions.count("invitation_confirmation_mismatch") == 4
    assert actions.count("invitation_confirmation_locked") == 1
    late = post(owner, f"/api/v1/family/invitations/{invitation_id}/confirm", {"code": confirmation["code"]})
    assert late.status_code == 409


@pytest.mark.parametrize("by", ["code", "link"])
def test_confirm_racing_reissue_never_uses_a_replaced_code(tmp_path, by):
    app = file_app(tmp_path)
    owner = app.test_client()
    register(owner, "招待する人")
    for round_number in range(ROUNDS):
        recipient, invitation_id, confirmation = requested(app, owner, f"申請する人{round_number}")
        confirmer, reissuer = session_client(app, owner), session_client(app, recipient)
        if by == "code":
            confirm = lambda: post(confirmer, f"/api/v1/family/invitations/{invitation_id}/confirm",  # noqa: E731
                                   {"code": confirmation["code"]})
        else:
            confirm = lambda: post(confirmer, "/api/v1/family/invitations/confirm-link",  # noqa: E731
                                   {"token": confirmation["token"]})
        reissue = lambda: post(reissuer, f"/api/v1/family/invitations/{invitation_id}/confirmation")  # noqa: E731
        (confirm_status, _), (reissue_status, _) = run_together(confirm, reissue)
        status, _failures, connections = state(app, invitation_id)
        assert not (confirm_status == 200 and reissue_status == 200), (round_number, confirm_status, reissue_status)
        if confirm_status == 200:
            assert (status, connections) == ("accepted", 1)
        else:
            assert reissue_status == 200
            assert (status, connections) == ("awaiting_sender", 0)


@pytest.mark.parametrize("closer", ["recipient", "sender"])
def test_confirm_racing_cancel_leaves_one_consistent_outcome(tmp_path, closer):
    app = file_app(tmp_path)
    owner = app.test_client()
    register(owner, "招待する人")
    for round_number in range(ROUNDS):
        recipient, invitation_id, confirmation = requested(app, owner, f"申請する人{round_number}")
        confirmer = session_client(app, owner)
        closing = session_client(app, recipient if closer == "recipient" else owner)
        confirm = lambda: post(confirmer, f"/api/v1/family/invitations/{invitation_id}/confirm",  # noqa: E731
                               {"code": confirmation["code"]})
        close = lambda: post(closing, f"/api/v1/family/invitations/{invitation_id}/reject")  # noqa: E731
        (confirm_status, _), (close_status, _) = run_together(confirm, close)
        status, _failures, connections = state(app, invitation_id)
        assert not (confirm_status == 200 and close_status == 200), (round_number, confirm_status, close_status)
        if confirm_status == 200:
            assert (status, connections) == ("accepted", 1)
        else:
            assert close_status == 200
            assert status in {"rejected", "cancelled"} and connections == 0
