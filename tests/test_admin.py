"""Администратор: вход, права доступа на сервере, шаблоны, база знаний, обращения, статистика, история изменений."""

import io
import zipfile

import pytest
from docx import Document
from fastapi.testclient import TestClient

from conftest import ADMIN_PASS, ADMIN_USER, admin_login, make_settings
from dekanat.admin.security import LoginThrottle, check_password_policy, hash_password, verify_password
from dekanat.templates.sample import build_sample_template
from dekanat.web.app import create_app


@pytest.fixture()
def app(tmp_settings):
    return create_app(tmp_settings)


@pytest.fixture()
def anon(app):
    return TestClient(app)


@pytest.fixture()
def admin(app):
    c = TestClient(app)
    admin_login(c, app)
    return c


@pytest.fixture()
def docx_bytes(tmp_path):
    return build_sample_template(tmp_path / "sample.docx").read_bytes()


ADMIN_ROUTES = [
    ("GET", "/api/admin/me"), ("GET", "/api/admin/info"), ("GET", "/api/admin/templates"), ("POST", "/api/admin/templates"), ("GET", "/api/admin/templates/x"),
    ("PUT", "/api/admin/templates/x"), ("PATCH", "/api/admin/templates/x"), ("DELETE", "/api/admin/templates/x"), ("GET", "/api/admin/templates/x/download"),
    ("GET", "/api/admin/templates/x/history"), ("GET", "/api/admin/kb"), ("POST", "/api/admin/kb"), ("GET", "/api/admin/kb/base"), ("PUT", "/api/admin/kb/base"),
    ("PATCH", "/api/admin/kb/base"), ("DELETE", "/api/admin/kb/d1"), ("POST", "/api/admin/reindex"), ("GET", "/api/admin/kb/base/history"), ("GET", "/api/admin/tickets"),
    ("GET", "/api/admin/tickets/1"), ("PATCH", "/api/admin/tickets/1"), ("GET", "/api/admin/tickets/1/attachment"), ("GET", "/api/admin/stats"), ("GET", "/api/admin/audit"),
    ("POST", "/api/admin/purge"), ("POST", "/api/admin/password"),
]


# ============================================================================ пароли
def test_password_hashing_and_policy():
    h = hash_password("Correct-Horse-9")
    assert h.startswith("scrypt$") and "Correct-Horse-9" not in h and verify_password("Correct-Horse-9", h)
    assert not verify_password("correct-horse-9", h) and not verify_password("x", "мусор") and hash_password("Correct-Horse-9") != h  # соль у каждого своя
    assert check_password_policy("short1!") and check_password_policy("1234567890123") and check_password_policy("admin", "admin") and check_password_policy("password")
    assert check_password_policy("Correct-Horse-9", "bob") is None


def test_throttle_is_off_by_default():
    t = LoginThrottle()
    for _ in range(50):
        t.failure("u:bob", "ip:1.2.3.4")
    assert t.remaining("u:bob", "ip:1.2.3.4") == 0 and not t.enabled


def test_throttle_locks_after_repeated_failures():
    t = LoginThrottle(3, 60)
    for _ in range(3):
        t.failure("u:bob")
    assert t.remaining("u:bob") > 0 and t.remaining("u:alice") == 0
    t.success("u:bob")
    assert t.remaining("u:bob") == 0


def test_no_default_admin_exists(app):
    assert app.state.storage.admins.count() == 0
    c = TestClient(app)
    for pw in ("admin", "password", "", "dekanat"):
        assert c.post("/api/admin/login", json={"username": "admin", "password": pw}).status_code == 401


# ============================================================================ вход и права
def test_login_logout_and_cookie_flags(app, anon):
    app.state.auth.create_admin(ADMIN_USER, ADMIN_PASS)
    bad = anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong-password-1"})
    assert bad.status_code == 401 and "Неверный логин или пароль" in bad.json()["detail"]
    assert anon.post("/api/admin/login", json={"username": "nobody", "password": "wrong-password-1"}).json()["detail"] == bad.json()["detail"]  # логин не выдаётся
    ok = anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert ok.status_code == 200 and ok.json() == {"username": ADMIN_USER, "role": "admin"}
    cookie = ok.headers["set-cookie"].lower()
    assert "dekanat_admin=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert ADMIN_PASS not in ok.text and anon.get("/api/admin/me").json()["username"] == ADMIN_USER
    assert anon.post("/api/admin/logout").status_code == 200
    assert anon.get("/api/admin/me").status_code == 401  # сессия на сервере уничтожена


def test_token_is_stored_only_as_a_hash(app, anon):
    app.state.auth.create_admin(ADMIN_USER, ADMIN_PASS)
    anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    token = anon.cookies.get("dekanat_admin")
    rows = app.state.storage.db.query("SELECT token_hash FROM admin_sessions")
    assert token and len(rows) == 1 and rows[0]["token_hash"] != token and token not in rows[0]["token_hash"]
    assert ADMIN_PASS not in str(app.state.storage.db.query("SELECT pw_hash FROM admins")[0]["pw_hash"])


def test_wrong_password_never_locks_the_login_by_default(app, anon):
    """Неверный пароль просто отклоняется: никакого ожидания, верный пароль принимается сразу после любого числа ошибок."""
    app.state.auth.create_admin(ADMIN_USER, ADMIN_PASS)
    for _ in range(12):
        assert anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong-password-1"}).status_code == 401
    assert anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    entries = app.state.storage.audit.list("admin", None, 50, 0)
    assert sum(1 for e in entries if e["action"] == "admin.login_failed") == 12  # попытки по-прежнему попадают в историю


def test_lockout_can_still_be_switched_on(tmp_settings):
    tmp_settings.raw["admin"].update(max_login_failures=5, lockout_minutes=15)
    app = create_app(tmp_settings)
    app.state.auth.create_admin(ADMIN_USER, ADMIN_PASS)
    anon = TestClient(app)
    for _ in range(5):
        assert anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": "wrong-password-1"}).status_code == 401
    locked = anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})  # даже верный пароль в блокировке не принимается
    assert locked.status_code == 429 and "Повторите" in locked.json()["detail"]


@pytest.mark.parametrize("method, path", ADMIN_ROUTES)
def test_every_admin_route_requires_admin_on_the_server(anon, method, path):
    r = anon.request(method, path)
    assert r.status_code == 401, (method, path, r.status_code)


@pytest.mark.parametrize("method, path", [(m, p) for m, p in ADMIN_ROUTES if m == "GET"])
def test_user_session_does_not_grant_admin_rights(app, method, path):
    user = TestClient(app)
    assert user.get("/api/assistant/state").status_code == 200 and "dekanat_sid" in user.cookies  # обычный пользователь с живой сессией
    assert user.request(method, path).status_code == 401
    # подделка cookie администратора тоже не работает
    user.cookies.set("dekanat_admin", "x" * 43)
    assert user.request(method, path).status_code == 401


def test_cross_site_requests_are_rejected(admin):
    r = admin.post("/api/admin/reindex", headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and "Origin" in r.json()["detail"]
    assert admin.post("/api/admin/reindex", headers={"Origin": "http://testserver"}).status_code == 200


def test_password_change_ends_other_sessions(app, admin):
    other = TestClient(app)
    other.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert admin.post("/api/admin/password", json={"current": "wrong", "new": "Another-Pass-77"}).status_code == 400
    assert admin.post("/api/admin/password", json={"current": ADMIN_PASS, "new": "short"}).status_code == 400
    assert admin.post("/api/admin/password", json={"current": ADMIN_PASS, "new": "Another-Pass-77"}).status_code == 200
    assert admin.get("/api/admin/me").status_code == 200 and other.get("/api/admin/me").status_code == 401
    assert TestClient(app).post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 401


def test_admin_from_environment(tmp_settings, tmp_path_factory, monkeypatch):
    monkeypatch.setenv("DEKANAT_ADMIN_USERNAME", "env-admin")
    monkeypatch.setenv("DEKANAT_ADMIN_PASSWORD", "Env-Pass-12345")
    app = create_app(tmp_settings)
    assert TestClient(app).post("/api/admin/login", json={"username": "env-admin", "password": "Env-Pass-12345"}).status_code == 200
    monkeypatch.setenv("DEKANAT_ADMIN_PASSWORD", "weak")  # слабый пароль не принимается
    other = create_app(make_settings(tmp_path_factory.mktemp("other")))
    assert other.state.storage.admins.count() == 0


# ============================================================================ шаблоны
def upload(c, data, name="Заявление.docx", **form):
    return c.post("/api/admin/templates", files={"file": (name, data, "application/octet-stream")}, data=form)


def test_template_lifecycle(admin, docx_bytes):
    r = upload(admin, docx_bytes, name="perevod.docx", name_="x") if False else upload(admin, docx_bytes, name="perevod.docx")
    assert r.status_code == 200
    info = r.json()
    tid = info["template_id"]
    assert info["name"] == "perevod" and "student_name" in info["standard_keys"] and info["version"] == 1
    assert [t["id"] for t in admin.get("/api/admin/templates").json()] == [tid]
    got = admin.get(f"/api/admin/templates/{tid}").json()
    assert got["history"][0]["version"] == 1
    dl = admin.get(f"/api/admin/templates/{tid}/download")
    assert dl.status_code == 200 and dl.content == docx_bytes
    assert admin.patch(f"/api/admin/templates/{tid}", json={"name": "Новое имя"}).json()["name"] == "Новое имя"
    # замена файла создаёт новую версию, прежняя остаётся в истории
    r2 = admin.put(f"/api/admin/templates/{tid}", files={"file": ("v2.docx", docx_bytes, "application/octet-stream")})
    assert r2.status_code == 200 and r2.json()["version"] == 2
    hist = admin.get(f"/api/admin/templates/{tid}/history").json()
    assert [v["version"] for v in hist["versions"]] == [2, 1] and {e["action"] for e in hist["log"]} >= {"template.create", "template.replace"}
    assert admin.delete(f"/api/admin/templates/{tid}").status_code == 200
    assert admin.get(f"/api/admin/templates/{tid}").status_code == 404 and admin.delete(f"/api/admin/templates/{tid}").status_code == 404
    assert admin.get("/api/admin/templates").json() == []


def test_template_upload_validation(admin, docx_bytes):
    assert upload(admin, b"not a docx", name="a.docx").status_code == 400
    assert upload(admin, docx_bytes, name="a.txt").status_code == 400
    assert upload(admin, b"", name="a.docx").status_code == 400
    buf = io.BytesIO(docx_bytes)
    with zipfile.ZipFile(buf, "a") as z:
        z.writestr("word/vbaProject.bin", b"x")
    r = upload(admin, buf.getvalue(), name="macro.docx")
    assert r.status_code == 400 and "макрос" in r.json()["detail"]


def test_existing_template_needs_explicit_replace(admin, docx_bytes):
    tid = upload(admin, docx_bytes, template_id="perevod").json()["template_id"]
    again = upload(admin, docx_bytes, template_id="perevod")
    assert again.status_code == 409 and "перезапись" in again.json()["detail"]
    ok = upload(admin, docx_bytes, template_id="perevod", replace="true")
    assert ok.status_code == 200 and ok.json()["version"] == 2 and tid == "perevod"
    assert upload(admin, docx_bytes).json()["template_id"] != "perevod"  # без id создаётся новый шаблон, чужой не затирается


def test_user_gets_a_clear_message_when_template_is_deleted_mid_flow(app, admin, docx_bytes):
    tid = upload(admin, docx_bytes, template_id="perevod").json()["template_id"]
    user = TestClient(app)
    for text in ("Я хочу перевестись", "да", "Иванов Иван Иванович", "ЮФУ", "2", "09.03.01 Информатика", "очная", "нет", "пропустить", "пропустить", "верно"):
        user.post("/api/assistant/message", json={"text": text})
    assert admin.delete(f"/api/admin/templates/{tid}").status_code == 200
    r = user.post("/api/assistant/generate", json={"template_id": tid})
    assert r.status_code == 404 and "Шаблон не найден" in r.json()["detail"]
    assert user.get("/api/assistant/state").json()["dialog"]["state"] == "document_generation"  # анкета не потеряна


# ============================================================================ база знаний
def kb_text(n_days=14):
    return f"## 1. Практика\n1.1. Отчёт по практике сдаётся в течение {n_days} календарных дней после её окончания.\n1.2. Руководитель практики от вуза назначается приказом.\n"


def test_kb_document_is_indexed_cited_and_removed(admin):
    assert admin.post("/api/analyze", data={"question": "В какой срок сдаётся отчёт по практике?"}).json()["answer"]["verdict"] == "no_answer"
    r = admin.post("/api/admin/kb", files={"file": ("practice.md", kb_text().encode(), "text/markdown")}, data={"name": "Регламент практики"})
    assert r.status_code == 200 and r.json()["clauses"] == 2 and r.json()["version"] == 1
    doc_id = r.json()["id"]
    a = admin.post("/api/analyze", data={"question": "В какой срок сдаётся отчёт по практике?"}).json()["answer"]
    assert a["verdict"] == "ok" and a["sources"][0]["ref"] == f"{doc_id}:1.1" and "Регламент практики" in a["sources"][0]["label"] and "14 календарных" in a["text"]
    assert a["report"]["clauses"][0]["ref"] == f"{doc_id}:1.1"
    # основное Положение по-прежнему отвечает со своей нумерацией
    base = admin.post("/api/analyze", data={"question": "Сколько дней рассматривается заявление?"}).json()["answer"]
    assert base["sources"][0]["ref"] == "6.1" and base["text"].endswith("(п. 6.1 Положения)")
    assert any(c["document"]["name"] == "Регламент практики" for c in admin.get("/api/regulation").json()["clauses"] if "document" in c)
    # отключённый документ не выдаётся как действующий
    admin.patch(f"/api/admin/kb/{doc_id}", json={"active": False})
    assert admin.post("/api/analyze", data={"question": "В какой срок сдаётся отчёт по практике?"}).json()["answer"]["verdict"] == "no_answer"
    admin.patch(f"/api/admin/kb/{doc_id}", json={"active": True})
    # замена: старый пункт исчезает, новый появляется, старая версия остаётся в истории
    rep = admin.put(f"/api/admin/kb/{doc_id}", files={"file": ("practice.md", kb_text(30).encode(), "text/markdown")})
    assert rep.status_code == 200 and rep.json()["version"] == 2
    text = admin.post("/api/analyze", data={"question": "В какой срок сдаётся отчёт по практике?"}).json()["answer"]["text"]
    assert "30 календарных" in text and "14 календарных" not in text
    hist = admin.get(f"/api/admin/kb/{doc_id}/history").json()
    assert [v["version"] for v in hist["versions"]] == [2, 1] and {e["action"] for e in hist["log"]} >= {"kb.create", "kb.replace", "kb.update"}
    # удаление
    assert admin.delete(f"/api/admin/kb/{doc_id}").status_code == 200
    assert admin.post("/api/analyze", data={"question": "В какой срок сдаётся отчёт по практике?"}).json()["answer"]["verdict"] == "no_answer"
    assert not any("document" in c for c in admin.get("/api/regulation").json()["clauses"])
    assert admin.get(f"/api/admin/kb/{doc_id}").status_code == 404


def test_kb_validation(admin):
    def post(name, data, title="Док"):
        return admin.post("/api/admin/kb", files={"file": (name, data, "application/octet-stream")}, data={"name": title})

    assert post("a.exe", b"MZ").status_code == 400
    assert post("a.md", b"").status_code == 400
    assert post("a.md", b"  ").status_code == 400
    assert post("a.docx", b"not a docx").status_code == 400
    assert post("a.pdf", b"not a pdf").status_code == 400
    assert post("a.md", kb_text().encode()).status_code == 200
    dup = post("b.md", kb_text().encode())
    assert dup.status_code == 400 and "уже есть" in dup.json()["detail"]
    assert admin.post("/api/admin/kb", files={"file": ("x.md", kb_text().encode(), "text/plain")}).status_code == 200  # название берётся из имени файла


def test_base_regulation_replacement_is_versioned_and_can_be_reset(admin):
    before = admin.get("/api/admin/kb").json()
    assert before[0]["id"] == "base" and before[0]["clauses"] == 25 and before[0]["from_file"] is True
    text = "1.1. Деканат выдаёт справки в течение 7 рабочих дней.\n1.2. Заявления рассматривает декан.\n"
    assert admin.put("/api/regulation", files={"file": ("новое.md", text.encode(), "text/markdown")}).json()["clauses"] == 2
    row = admin.get("/api/admin/kb").json()[0]
    assert row["id"] == "base" and row["clauses"] == 2 and row["version"] == 2
    versions = admin.get("/api/admin/kb/base/history").json()["versions"]
    assert [v["version"] for v in versions] == [2, 1] and versions[1]["clauses"] == 25  # исходный файл сохранён как версия 1
    assert admin.delete("/api/admin/kb/base").json()["action"] == "kb.reset_base"
    assert len(admin.get("/api/regulation").json()["clauses"]) == 25


def test_reindex_endpoint_and_replacement_persist_after_restart(tmp_settings, docx_bytes):
    app1 = create_app(tmp_settings)
    c = TestClient(app1)
    admin_login(c, app1)
    c.post("/api/admin/kb", files={"file": ("p.md", kb_text().encode(), "text/markdown")}, data={"name": "Регламент практики"})
    info = c.post("/api/admin/reindex").json()
    assert info["clauses"] == 27 and info["documents"] == 2
    app2 = create_app(tmp_settings)  # «перезапуск сервера»: документы берутся из хранилища
    assert len(app2.state.svc.clauses) == 27


# ============================================================================ обращения и статистика
def seed_ticket(app, type_="transfer", topic="Перевод из другого вуза", key="k1"):
    return app.state.storage.tickets.create("ref1", type_, topic, {"request": "перевод", "fields": {"ФИО": "Иванов"}}, "queue", key)[0]


def test_tickets_filters_and_status_changes(app, admin):
    a = seed_ticket(app, key="a")
    b = seed_ticket(app, "enrollment", "Поступление и зачисление", key="b")
    app.state.storage.tickets.update(a["id"], "in_progress")
    res = admin.get("/api/admin/tickets").json()
    assert res["total"] == 2 and {t["id"] for t in res["items"]} == {a["id"], b["id"]} and "payload" not in res["items"][0]
    assert [t["id"] for t in admin.get("/api/admin/tickets?type=enrollment").json()["items"]] == [b["id"]]
    assert [t["id"] for t in admin.get("/api/admin/tickets?status=in_progress").json()["items"]] == [a["id"]]
    assert [t["id"] for t in admin.get("/api/admin/tickets?topic=Поступление и зачисление").json()["items"]] == [b["id"]]
    assert res["counts"] == {"new": 1, "in_progress": 1, "done": 0, "rejected": 0}
    only = admin.get("/api/admin/tickets?status=in_progress").json()  # счётчики не зависят от выбранного статуса, но учитывают остальные фильтры
    assert only["counts"] == res["counts"] and admin.get("/api/admin/tickets?type=enrollment").json()["counts"]["new"] == 1
    assert admin.get("/api/admin/tickets?date_from=2999-01-01").json()["total"] == 0
    assert admin.get("/api/admin/tickets?date_from=2000-01-01&date_to=2999-01-01").json()["total"] == 2
    assert admin.get("/api/admin/tickets?date_from=вчера").status_code == 400
    assert admin.get("/api/admin/tickets?type=bogus").status_code == 400 and admin.get("/api/admin/tickets?status=bogus").status_code == 400
    one = admin.get(f"/api/admin/tickets/{a['id']}").json()
    assert one["payload"]["fields"]["ФИО"] == "Иванов"
    upd = admin.patch(f"/api/admin/tickets/{a['id']}", json={"status": "done", "note": "принято"}).json()
    assert upd["status"] == "done" and upd["note"] == "принято"
    assert admin.patch(f"/api/admin/tickets/{a['id']}", json={"status": "bogus"}).status_code == 400
    assert admin.patch("/api/admin/tickets/9999", json={"status": "done"}).status_code == 404


def test_tickets_export_csv_with_filters_and_without_formula_injection(app, admin, anon):
    a = seed_ticket(app, key="a")
    b = app.state.storage.tickets.create("ref2", "enrollment", "Поступление и зачисление", {"request": "зачисление", "fields": {"ФИО": '=HYPERLINK("http://x")', "Контакт": "ivan@example.com"}}, "queue", "b")[0]
    app.state.storage.tickets.update(b["id"], "in_progress", "+заметка", "Приходите в среду")
    assert anon.get("/api/admin/tickets/export.csv").status_code == 401
    r = admin.get("/api/admin/tickets/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv") and "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    lines = text.strip().split("\r\n")
    assert text.startswith("﻿") and lines[0].lstrip("﻿").startswith("№;Дата (UTC);Тип;Тема;Статус;ФИО")
    rows = [line.split(";") for line in lines[1:]]
    assert len(rows) == 2 and rows[0][0] == str(b["id"]) and rows[0][2] == "зачисление" and rows[0][4] == "в работе"
    assert "'=HYPERLINK" in text and "ivan@example.com" in text and "'+заметка" in text and "Приходите в среду" in text  # формулы обезврежены
    only = admin.get("/api/admin/tickets/export.csv?type=transfer").content.decode("utf-8").strip().split("\r\n")
    assert len(only) == 2 and only[1].startswith(str(a["id"]) + ";")
    assert admin.get("/api/admin/tickets/export.csv?status=bogus").status_code == 400
    assert any(e["action"] == "ticket.export" for e in app.state.storage.audit.list("ticket", limit=20))


def test_stats_count_requests_topics_and_screenings(app, admin):
    user = TestClient(app)
    user.get("/")
    user.get("/assistant")  # прежняя ссылка ведёт на стол и тоже считается посещением
    admin.post("/api/analyze", data={"question": "Сколько дней рассматривается заявление?"})
    admin.post("/api/analyze", data={"question": "Нужна ли медицинская справка при академическом отпуске?"})
    admin.post("/api/analyze", data={"question": "Сколько стоит обучение?"})
    user.post("/api/assistant/message", json={"text": "Я хочу перевестись"})
    user.post("/api/assistant/message", json={"text": "да"})
    s = admin.get("/api/admin/stats?days=7").json()
    t = s["totals"]
    assert t["visits"] == 2 and t["questions"] == 3 and t["screenings_transfer"] == 1 and t["screenings_enrollment"] == 0
    topics = {x["topic"]: x["count"] for x in s["topics"]}
    assert topics["Сроки рассмотрения"] == 1 and topics["Академический отпуск"] == 1 and topics["Стоимость и стипендия"] == 1
    assert s["statuses"]["ok"] == 2 and s["statuses"]["no_answer"] == 1 and s["by_day"][-1]["question"] == 3


def test_stats_hold_no_personal_data(app, admin):
    user = TestClient(app)
    user.post("/api/analyze", data={"question": "Меня зовут Иванов Иван, паспорт 1234 567890, можно ли перевестись?"})
    for text in ("Я хочу перевестись", "да", "Иванов Иван Иванович", "Южный федеральный университет"):
        user.post("/api/assistant/message", json={"text": text})
    dump = " ".join(str(dict(r)) for r in app.state.storage.db.query("SELECT * FROM events"))
    for secret in ("Иванов", "1234", "567890", "Южный", "паспорт"):
        assert secret not in dump
    sid = user.cookies.get("dekanat_sid")
    assert sid not in dump  # вместо идентификатора сессии — необратимый хэш


def test_audit_log_records_admin_actions_without_secrets(admin, docx_bytes):
    upload(admin, docx_bytes, template_id="perevod")
    admin.post("/api/admin/reindex")
    log = admin.get("/api/admin/audit").json()
    actions = [e["action"] for e in log]
    assert "admin.login" in actions and "template.create" in actions and "kb.reindex" in actions
    assert ADMIN_PASS not in str(log)
    assert admin.get("/api/admin/audit?object_type=template&object_id=perevod").json()[0]["object_id"] == "perevod"


def test_doctype_changes_are_audited(admin):
    tpl = admin.get("/api/doctypes/template").text
    assert admin.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"}).status_code == 200
    assert admin.delete("/api/doctypes/my_document").status_code == 200
    actions = [e["action"] for e in admin.get("/api/admin/audit?object_type=doctype").json()]
    assert "doctype.save" in actions and "doctype.delete" in actions


def test_info_reports_delivery_and_llm(app, admin):
    info = admin.get("/api/admin/info").json()
    assert info["llm"]["enabled"] is False and info["delivery"]["available"] is True and info["templates"] == 0
    assert info["regulation"]["clauses"] == 25


def test_pages_are_served_with_strict_csp(anon):
    assert anon.get("/static/pages/assistant.js").status_code == 404  # отдельной страницы ассистента больше нет: всё на столе
    for path in ("/admin",):
        r = anon.get(path)
        assert r.status_code == 200 and "script-src 'self'" in r.headers["content-security-policy"] and "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "<script>" not in r.text and "style=" not in r.text  # встроенных скриптов и стилей нет
        for src in __import__("re").findall(r'(?:src|href)="(/static/pages/[^"]+)"', r.text):
            asset = anon.get(src)
            assert asset.status_code == 200
            assert asset.headers["cache-control"] == "no-cache"  # после обновления браузер не возьмёт старый скрипт из кеша
    assert anon.get("/api/health").headers["x-content-type-options"] == "nosniff"


# ---- забытый пароль: сброс ответственным за сервер
def test_reset_password_closes_old_sessions_and_changes_the_password(app, anon):
    auth = app.state.auth
    auth.create_admin(ADMIN_USER, ADMIN_PASS)
    assert anon.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 200
    assert anon.get("/api/admin/me").status_code == 200
    auth.reset_password(ADMIN_USER, "Brand-New-Pass-77!")
    assert anon.get("/api/admin/me").status_code == 401  # прежний вход завершён
    fresh = TestClient(app)
    assert fresh.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}).status_code == 401
    assert fresh.post("/api/admin/login", json={"username": ADMIN_USER, "password": "Brand-New-Pass-77!"}).status_code == 200
    assert any(e["action"] == "admin.password_reset" for e in app.state.storage.audit.list("admin", None, 20, 0))


def test_reset_password_checks_the_policy_and_the_account(app):
    auth = app.state.auth
    auth.create_admin(ADMIN_USER, ADMIN_PASS)
    with pytest.raises(ValueError, match="Администратора"):
        auth.reset_password("nobody", "Brand-New-Pass-77!")
    with pytest.raises(ValueError):
        auth.reset_password(ADMIN_USER, "short")
    assert app.state.storage.admins.get_by_username(ADMIN_USER)["pw_hash"]  # прежний хэш цел после отказа


def test_cli_create_and_reset_admin(tmp_path, monkeypatch, capsys):
    from dekanat.cli import main as cli

    monkeypatch.setenv("DEKANAT_STORAGE_DB_PATH", str(tmp_path / "cli.db"))  # не трогаем рабочую базу проекта
    monkeypatch.setenv("TEST_PW_ONE", "First-Pass-12345!")
    monkeypatch.setenv("TEST_PW_TWO", "Second-Pass-12345!")
    assert cli(["create-admin", "--username", "dean", "--password-env", "TEST_PW_ONE"]) == 0
    assert cli(["create-admin", "--username", "dean", "--password-env", "TEST_PW_ONE"]) == 1
    err = capsys.readouterr().err
    assert "уже существует" in err and "reset-admin-password --username dean" in err  # подсказка, что делать, если пароль забыт
    assert cli(["reset-admin-password", "--username", "ghost", "--password-env", "TEST_PW_TWO"]) == 1
    assert "dean" in capsys.readouterr().err  # перечислены существующие учётные записи
    assert cli(["reset-admin-password", "--username", "dean", "--password-env", "TEST_PW_TWO"]) == 0

    from dekanat.admin.security import verify_password
    from dekanat.config import load_settings
    from dekanat.storage import Storage

    row = Storage(load_settings()).admins.get_by_username("dean")
    assert verify_password("Second-Pass-12345!", row["pw_hash"]) and not verify_password("First-Pass-12345!", row["pw_hash"])
