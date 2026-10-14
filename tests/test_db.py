from sqlalchemy import text

from app import create_app
from app.extensions import db
from config import TestingConfig


def test_init_db_keeps_existing_rows(tmp_path):
    db_path = tmp_path / "existing.db"

    class FileConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + db_path.as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {}

    application = create_app(FileConfig)
    runner = application.test_cli_runner()

    first = runner.invoke(args=["init-db"])
    assert first.exit_code == 0, first.output
    assert "削除していません" in first.output

    with application.app_context():
        db.session.execute(
            text("CREATE TABLE legacy_probe (id INTEGER PRIMARY KEY, note TEXT)")
        )
        db.session.execute(text("INSERT INTO legacy_probe (note) VALUES ('dummy')"))
        db.session.commit()

    second = runner.invoke(args=["init-db"])
    assert second.exit_code == 0, second.output

    with application.app_context():
        note = db.session.execute(text("SELECT note FROM legacy_probe")).scalar_one()
        db.session.execute(text("SELECT 1"))
    assert note == "dummy"


def test_health_reports_database_error(tmp_path):
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("dummy", encoding="utf-8")

    class BadConfig(TestingConfig):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + (blocked / "dev.db").as_posix()
        SQLALCHEMY_ENGINE_OPTIONS = {}

    application = create_app(BadConfig)
    response = application.test_client().get("/health")
    assert response.status_code == 503
    assert response.get_json() == {"status": "error", "database": "error"}
