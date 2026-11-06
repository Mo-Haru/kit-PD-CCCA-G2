"""The page renders without DB writes; persistence goes through the encrypted API."""

import json
import subprocess
from pathlib import Path

from tests.test_menu import StrictMarkup

ROOT = Path(__file__).resolve().parents[1]


def test_demo_questions_have_labels_and_stay_separate_from_submission(client):
    # No db.create_all(): the demo must not require persisted questions or a user.
    response = client.get("/answers/demo")
    assert response.status_code == 200
    markup = StrictMarkup(response.get_data(as_text=True))
    fixture = json.loads((ROOT / "app/data/answer-demo.json").read_text(encoding="utf-8"))
    questions = [element for element in markup.elements if "data-demo-question" in element.attrs]
    assert [question.attrs["data-question-id"] for question in questions] == [
        question["id"] for question in fixture["questions"]
    ]
    assert len(questions) == 6
    ids = [element.attrs["id"] for element in markup.elements if "id" in element.attrs]
    assert len(ids) == len(set(ids))
    form = markup.one_id("demo-answer-form")
    assert form.attrs["method"] == "dialog"
    assert "action" not in form.attrs
    assert "disabled" in markup.one_id("demo-fields").attrs
    assert "hidden" in markup.one_id("demo-review").attrs
    for question in questions:
        controls = [element for element in markup.elements
                    if question in element.ancestors and element.tag in {"input", "select", "textarea"}]
        assert controls
        for control in controls:
            assert any(label.attrs.get("for") == control.attrs["id"] for label in markup.tagged("label"))
            for helper_id in control.attrs.get("aria-describedby", "").split():
                markup.one_id(helper_id)
    markup.module_script("/static/js/watashi/answer-demo-page.mjs")
    assert "disabled" in markup.one_id("demo-save").attrs
    assert response.headers["Cache-Control"] == "no-store"
    assert client.post("/answers/demo", data={"demo-name": "dummy"}).status_code == 405
    assert client.get("/static/css/answers.css").status_code == 200
    assert client.get("/static/js/watashi/answer-demo.mjs").status_code == 200


def test_demo_entry_is_available_from_home_and_answers(client):
    for path in ("/", "/answers"):
        markup = StrictMarkup(client.get(path).get_data(as_text=True))
        assert any(link.attrs.get("href") == "/answers/demo" for link in markup.tagged("a"))


def test_demo_input_review_edit_and_reset():
    result = subprocess.run(
        ["node", "tests/answer_demo.mjs"], cwd=ROOT,
        capture_output=True, text=True, timeout=20, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_question_seed_is_idempotent_and_preserves_existing_rows(app):
    from app.demo_answers import demo_catalog, seed_demo_questions
    from app.extensions import db
    from app.models import Question, QuestionCategory, QuestionOption

    with app.app_context():
        db.create_all()
        category = QuestionCategory(id="existing-category", name="既存の章", sort_order=10)
        db.session.add(category)
        db.session.flush()
        db.session.add(Question(id="existing-question", category_id=category.id, question_key="existing",
                                text="既存の質問", answer_type="text", is_active=True, sort_order=20))
        db.session.commit()
        assert seed_demo_questions() == 20  # 2 categories + 6 questions + 12 options
        assert seed_demo_questions() == 0
        result = app.test_cli_runner().invoke(args=["seed-answer-demo"])
        assert result.exit_code == 0
        app.config["DEMO_QUESTIONS_ENABLED"] = False
        blocked = app.test_cli_runner().invoke(args=["seed-answer-demo"])
        assert blocked.exit_code != 0
        assert "開発・テスト環境専用" in blocked.output
        assert db.session.query(Question).count() == 7
        assert db.session.query(QuestionOption).count() == 12
        existing = db.session.get(Question, "existing-question")
        assert existing.text == "既存の質問" and existing.sort_order == 20 and existing.is_active
        assert db.session.query(Question).filter(Question.id != existing.id).order_by(Question.sort_order).first().sort_order > 20
        first = db.session.get(Question, demo_catalog()["questions"][0]["id"])
        first.text = "他の担当が変更した質問"
        db.session.commit()
        import pytest
        with pytest.raises(ValueError, match="一致しません"):
            seed_demo_questions()
        assert db.session.get(Question, first.id).text == "他の担当が変更した質問"


def test_acp_question_seed_adds_questions_options_and_glossary_once(app, client):
    from app.demo_answers import acp_catalog
    from app.extensions import db
    from app.models import GlossaryTerm, Question, QuestionGlossaryTerm, QuestionOption

    with app.app_context():
        db.create_all()
        catalog = acp_catalog()
        options = sum(len(question.get("options", [])) for question in catalog["questions"])
        # 本番でも使う質問なので、開発・テスト専用の設定がなくても登録できる。
        app.config["DEMO_QUESTIONS_ENABLED"] = False
        first = app.test_cli_runner().invoke(args=["seed-questions"])
        assert first.exit_code == 0, first.output
        assert "22問" in first.output
        assert db.session.query(Question).count() == 22
        assert db.session.query(QuestionOption).count() == options == 21 * 5 + 1
        assert {row.term for row in db.session.query(GlossaryTerm)} == {"人生会議（ACP）", "ケアマネジャー"}
        link = db.session.query(QuestionGlossaryTerm).one()
        assert db.session.get(Question, link.question_id).text == "生活の手助けが必要になったら、誰に相談したいですか？"
        again = app.test_cli_runner().invoke(args=["seed-questions"])
        assert "追加した質問・章・選択肢・用語: 0件" in again.output
        free = db.session.get(Question, catalog["questions"][-1]["id"])
        assert free.answer_type == "text" and free.question_key == "acp-free"
    listed = client.get("/api/v1/answers/questions").get_json()
    assert [question["id"] for question in listed["questions"]] == [question["id"] for question in catalog["questions"]]
