"""Кабинет: проверка базы знаний по контрольным вопросам, заготовки ответа студенту, предупреждение о возможных дублях."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from conftest import admin_login
from dekanat.kb.check import load_golden, run_check
from dekanat.web.app import create_app


@pytest.fixture()
def app(tmp_settings):
    return create_app(tmp_settings)


@pytest.fixture()
def admin(app):
    c = TestClient(app)
    admin_login(c, app)
    return c


# ============================================================================ проверка базы знаний
def answer(verdict, *refs):
    return SimpleNamespace(verdict=verdict, sources=[SimpleNamespace(kind="clause", ref=r) for r in refs])


def test_golden_file_is_read_and_bad_rows_are_skipped(tmp_path):
    p = tmp_path / "g.yaml"
    p.write_text('questions:\n  - {question: "А?", ref: "1.1"}\n  - {question: "Б?", no_answer: true}\n  - {question: "", ref: "2.2"}\n  - {ref: "3.3"}\n  - {question: "В?"}\n  - just text\n', encoding="utf-8")
    assert load_golden(p) == [{"question": "А?", "ref": "1.1", "no_answer": False}, {"question": "Б?", "ref": "", "no_answer": True}]
    assert load_golden(tmp_path / "missing.yaml") == []
    (tmp_path / "broken.yaml").write_text("questions: [unclosed", encoding="utf-8")
    assert load_golden(tmp_path / "broken.yaml") == []


def test_run_check_compares_clause_and_honest_no_answer():
    items = [{"question": "q1", "ref": "6.1", "no_answer": False}, {"question": "q2", "ref": "7.2", "no_answer": False}, {"question": "q3", "ref": "", "no_answer": True},
             {"question": "q4", "ref": "", "no_answer": True}, {"question": "q5", "ref": "2.1", "no_answer": False}]
    given = {"q1": answer("ok", "6.1"), "q2": answer("ok", "7.3"), "q3": answer("no_answer"), "q4": answer("ok", "1.1"), "q5": answer("ok", "dorm:2.1")}  # пункт документа базы: «doc:2.1»
    res = run_check(lambda q: given[q], items)
    assert res["total"] == 5 and res["passed"] == 3
    assert [r["ok"] for r in res["items"]] == [True, False, True, False, True]
    assert res["items"][1]["expected"] == "п. 7.2" and res["items"][1]["got"] == "п. 7.3"
    assert res["items"][3]["expected"] == "нет ответа" and res["items"][3]["got"] == "п. 1.1"


def test_check_endpoint_passes_on_the_bundled_regulation_and_needs_login(app, admin):
    r = admin.post("/api/admin/kb/check")
    assert r.status_code == 200
    res = r.json()
    assert res["total"] >= 12 and res["passed"] == res["total"], [x for x in res["items"] if not x["ok"]]
    assert TestClient(app).post("/api/admin/kb/check").status_code == 401
    assert any(e["action"] == "kb.check" for e in app.state.storage.audit.list("kb", limit=20))


def test_check_notices_when_an_answer_breaks(app, admin):
    """Основное Положение заменили на другое: контрольные вопросы показывают, что ответы изменились."""
    other = "1.1. Деканат работает по будням.\n\n1.2. Справки выдаются бесплатно."
    r = admin.put("/api/admin/kb/base", files={"file": ("p.md", other.encode("utf-8"), "text/markdown")})
    assert r.status_code == 200, r.text
    res = admin.post("/api/admin/kb/check").json()
    assert res["passed"] < res["total"] and any(not x["ok"] for x in res["items"])


def test_check_without_questions_explains_what_to_do(tmp_settings):
    (tmp_settings.root / "config" / "golden_questions.yaml").unlink()
    app = create_app(tmp_settings)
    c = TestClient(app)
    admin_login(c, app)
    r = c.post("/api/admin/kb/check")
    assert r.status_code == 404 and "golden_questions.yaml" in r.json()["detail"]


# ============================================================================ заготовки ответа
def test_reply_templates_default_and_custom(tmp_settings):
    app = create_app(tmp_settings)
    c = TestClient(app)
    admin_login(c, app)
    default = c.get("/api/admin/reply-templates").json()["templates"]
    assert len(default) >= 3 and all(t["title"] and t["text"] for t in default)
    assert TestClient(app).get("/api/admin/reply-templates").status_code == 401
    (tmp_settings.root / "config" / "reply_templates.yaml").write_text('templates:\n  - {title: "Мой ответ", text: "Приходите во вторник."}\n  - {title: "", text: "без названия"}\n  - {title: "без текста"}\n', encoding="utf-8")
    assert c.get("/api/admin/reply-templates").json()["templates"] == [{"title": "Мой ответ", "text": "Приходите во вторник."}]
    (tmp_settings.root / "config" / "reply_templates.yaml").write_text("templates: [broken", encoding="utf-8")
    assert len(c.get("/api/admin/reply-templates").json()["templates"]) == len(default)  # битый файл — стандартные заготовки


# ============================================================================ возможные дубли
def make_ticket(app, name, key, type_="transfer"):
    return app.state.storage.tickets.create(f"ref-{key}", type_, "Перевод из другого вуза", {"request": "перевод", "fields": {"ФИО": name}}, "queue", key)[0]


def test_ticket_shows_other_requests_of_the_same_student(app, admin):
    a = make_ticket(app, "Иванов Иван Иванович", "a")
    b = make_ticket(app, "иванов  иван иванович", "b")  # регистр и лишние пробелы не важны
    c = make_ticket(app, "Петров Пётр", "c")
    d = make_ticket(app, "ИВАНОВ ИВАН ИВАНОВИЧ", "d")
    app.state.storage.tickets.update(b["id"], "done")
    one = admin.get(f"/api/admin/tickets/{d['id']}").json()
    assert [s["id"] for s in one["similar"]] == [b["id"], a["id"]] and {s["status"] for s in one["similar"]} == {"new", "done"}
    assert admin.get(f"/api/admin/tickets/{c['id']}").json()["similar"] == []
    assert all(d["id"] != s["id"] for s in one["similar"])  # само обращение в списке не участвует


def test_ticket_without_a_name_has_no_similar(app, admin):
    t = app.state.storage.tickets.create("r", "other", "Анкета", {"request": "x", "fields": {}}, "queue", "k")[0]
    assert admin.get(f"/api/admin/tickets/{t['id']}").json()["similar"] == []


# ============================================================================ история обращения
def test_ticket_history_lists_the_sending_and_every_staff_action_newest_first(app, admin):
    t = make_ticket(app, "Иванов Иван", "h1")
    assert [h["text"] for h in admin.get(f"/api/admin/tickets/{t['id']}").json()["history"]] == ["обращение отправлено студентом"]
    admin.patch(f"/api/admin/tickets/{t['id']}", json={"status": "in_progress"})
    admin.patch(f"/api/admin/tickets/{t['id']}", json={"reply": "Приходите в среду", "note": "позвонил студенту"})
    admin.patch(f"/api/admin/tickets/{t['id']}", json={"status": "done"})
    history = admin.get(f"/api/admin/tickets/{t['id']}").json()["history"]
    assert [h["text"] for h in history] == ["статус: обработано", "изменён ответ студенту, изменена заметка", "статус: в работе", "обращение отправлено студентом"]
    assert history[0]["actor"] == "test-admin" and history[-1]["actor"] == "student" and all(h["at"] for h in history)
    assert "Приходите" not in str(history) and "позвонил" not in str(history)  # в истории только факты, сами тексты не дублируются


def test_history_of_one_ticket_does_not_leak_into_another(app, admin):
    a, b = make_ticket(app, "Иванов Иван", "x1"), make_ticket(app, "Петров Пётр", "x2")
    admin.patch(f"/api/admin/tickets/{a['id']}", json={"status": "rejected"})
    assert [h["text"] for h in admin.get(f"/api/admin/tickets/{b['id']}").json()["history"]] == ["обращение отправлено студентом"]
