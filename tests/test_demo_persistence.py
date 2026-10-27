"""Cross-language crypto + real API flow on an isolated SQLite file, no dev DB."""

import json
import selectors
import subprocess
from datetime import timedelta
from pathlib import Path

from app import create_app
from app.demo_answers import demo_catalog, seed_demo_questions
from app.extensions import db
from app.models import AuditLog, FamilyShareOwnerCopy, FamilyShareVersion, PrivateAnswerDocument, QrShare, QrShareVersion, utcnow
from app.services.support import b64encode
from app.services import qr_service
from config import TestingConfig
from tests.soft_passkey import SoftPasskey

ROOT = Path(__file__).resolve().parents[1]


def test_fixture_persists_and_shares_through_real_crypto_and_apis(tmp_path, monkeypatch):
    class IsolatedConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (tmp_path / "demo-flow.db").as_posix()
        RATELIMIT_ENABLED = False

    app = create_app(IsolatedConfig)
    clients = {name: app.test_client() for name in ("A", "B", "C", "viewer", "other-viewer")}
    passkeys = {name: SoftPasskey() for name in ("A", "B", "C")}
    with app.app_context():
        db.create_all()
        seed_demo_questions()
        fixture = demo_catalog()
    clock = [utcnow()]
    monkeypatch.setattr(qr_service, "utcnow", lambda: clock[0])
    requests = []
    process = subprocess.Popen(
        ["node", "tests/demo_persistence_flow.mjs"], cwd=ROOT,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    outcome = None
    try:
        for _ in range(300):
            assert selector.select(timeout=15), "Node/Flask test bridge timed out"
            line = process.stdout.readline()
            assert line, "Node/Flask test bridge ended unexpectedly"
            message = json.loads(line)
            kind = message["kind"]
            if kind in {"done", "failed"}:
                outcome = message
                break
            if kind == "fixture":
                reply = {"fixture": fixture}
            elif kind == "credential":
                passkey = passkeys[message["actor"]]
                operation = passkey.register if message["operation"] == "register" else passkey.authenticate
                reply = operation(message["public_key"], "http://localhost")
            elif kind == "api":
                client = clients[message["actor"]]
                body = message.get("body")
                if body:
                    # Register/sign-in metadata is public, never include answer text or PRF output.
                    requests.append({"path": message["path"], "body": json.loads(body)})
                response = client.open(message["path"], method=message["method"],
                                       data=body, headers=message.get("headers", {}))
                csrf = client.get_cookie("wn_csrf")
                reply = {"status": response.status_code, "payload": response.get_json(), "csrf": csrf.value if csrf else ""}
            elif kind == "clock":
                clock[0] += timedelta(seconds=message["advance"])
                reply = {"now": clock[0].isoformat()}
            elif kind == "evidence":
                with app.app_context():
                    tables = {}
                    for model in (PrivateAnswerDocument, FamilyShareVersion, FamilyShareOwnerCopy, QrShare, QrShareVersion, AuditLog):
                        tables[model.__tablename__] = [
                            {column.name: b64encode(value) if isinstance(value, bytes) else str(value)
                             for column in model.__table__.columns
                             for value in [getattr(row, column.name)]}
                            for row in db.session.query(model).all()
                        ]
                    reply = {"tables": tables, "counts": {name: len(rows) for name, rows in tables.items()}, "requests": requests}
            else:
                raise AssertionError(f"Unexpected bridge operation: {kind}")
            process.stdin.write(json.dumps(reply, ensure_ascii=False) + "\n")
            process.stdin.flush()
        assert outcome and outcome["kind"] == "done", outcome
        assert process.wait(timeout=5) == 0
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)
    # Reopen the file through a fresh app/engine to verify actual persistence.
    reopened = create_app(IsolatedConfig)
    with reopened.app_context():
        assert db.session.query(PrivateAnswerDocument).count() == 5
        assert db.session.query(QrShare).count() == 4
        assert db.session.query(FamilyShareVersion).count() == 4
