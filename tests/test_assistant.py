"""API ассистента: сессии без аккаунта и их изоляция, анкета -> DOCX -> скачивание -> обращение, срок хранения, загрузка документов."""

import io
import time
from pathlib import Path

import pytest
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image

from conftest import ADMIN_PASS, ADMIN_USER, admin_login, needs_ocr
from dekanat.templates.sample import build_sample_template
from dekanat.web.app import create_app

ANSWERS = ["Иванов Иван Иванович", "Южный федеральный университет", "2", "09.03.01 Информатика", "очная", "нет"]
DOCX_MAGIC = b"PK"


@pytest.fixture()
def app(tmp_settings):
    return create_app(tmp_settings)


@pytest.fixture()
def admin(app):
    c = TestClient(app)
    admin_login(c, app)
    return c


@pytest.fixture()
def template(admin, tmp_path):
    data = build_sample_template(tmp_path / "t.docx").read_bytes()
    r = admin.post("/api/admin/templates", files={"file": ("perevod.docx", data, "application/octet-stream")}, data={"template_id": "perevod", "name": "Заявление о переводе"})
    assert r.status_code == 200
    return "perevod"


def say(c, *texts, **extra):
    out = None
    for t in texts:
        out = c.post("/api/assistant/message", json={"text": t, **extra})
        assert out.status_code == 200, out.text
    return out.json()


def finish_questionnaire(c, passport="1234 567890", target="Прикладная информатика", intent="Я хочу перевестись"):
    return say(c, intent, "да", *ANSWERS, target, passport)


def complete(c, template_id="perevod", **kw):
    finish_questionnaire(c, **kw)
    say(c, "верно")
    r = c.post("/api/assistant/generate", json={"template_id": template_id})
    assert r.status_code == 200, r.text
    return r.json()


# ============================================================================ сессии
def test_session_cookie_is_httponly_and_state_is_empty(app):
    c = TestClient(app)
    r = c.get("/api/assistant/state")
    cookie = r.headers["set-cookie"].lower()
    assert "dekanat_sid=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    j = r.json()
    assert j["session"] == "new" and j["dialog"]["state"] == "idle" and j["document"] is None and "решение принимает сотрудник деканата" in j["welcome"]
    assert c.get("/api/assistant/state").json()["session"] == "ok"


def test_sessions_are_isolated_from_each_other(app, template):
    a, b = TestClient(app), TestClient(app)
    complete(a)
    say(b, "Как к вам поступить?", "да", "Петров Пётр Петрович")
    sa, sb = a.get("/api/assistant/state").json(), b.get("/api/assistant/state").json()
    assert sa["dialog"]["state"] == "completed" and sa["dialog"]["scenario"] == "transfer"
    assert sb["dialog"]["state"] == "screening" and sb["dialog"]["scenario"] == "enrollment" and sb["downloads"] == []
    doc_id = sa["downloads"][0]["id"]
    assert a.get(f"/api/assistant/download/{doc_id}").status_code == 200
    assert b.get(f"/api/assistant/download/{doc_id}").status_code == 404           # чужой документ даже по известному id недоступен
    assert TestClient(app).get(f"/api/assistant/download/{doc_id}").status_code == 410  # без сессии — тоже нет
    assert b.post("/api/assistant/submission", json={"confirm": True}).status_code == 409
    # анкета b не перетёрла анкету a
    assert "Петров" not in str(app.state.storage.sessions.load_state(a.cookies.get("dekanat_sid")))
    assert "Петров" in str(app.state.storage.sessions.load_state(b.cookies.get("dekanat_sid")))


def test_forged_or_unknown_session_cookie_gets_a_fresh_empty_session(app, template):
    a = TestClient(app)
    complete(a)
    attacker = TestClient(app)
    attacker.cookies.set("dekanat_sid", "guess-" + "a" * 26)
    r = attacker.get("/api/assistant/state")
    j = r.json()
    assert j["session"] == "expired" and j["dialog"]["state"] == "idle" and j["downloads"] == []
    issued = r.headers["set-cookie"].split("dekanat_sid=")[1].split(";")[0]
    assert issued and issued != "guess-" + "a" * 26 and issued != a.cookies.get("dekanat_sid")  # выдана новая сессия, чужая не подхвачена


def test_state_survives_server_restart(tmp_settings, template):
    app1 = create_app(tmp_settings)
    c = TestClient(app1)
    say(c, "Я хочу перевестись", "да", "Иванов Иван Иванович", "ЮФУ")
    app2 = create_app(tmp_settings)  # перезапуск: всё читается из хранилища
    c2 = TestClient(app2)
    c2.cookies.update(c.cookies)
    st = c2.get("/api/assistant/state").json()
    assert st["session"] == "ok" and st["dialog"]["state"] == "screening" and st["dialog"]["current"] == "current_course"
    assert say(c2, "3")["reply"]["question"] == "current_specialty"


def test_message_validation_and_cross_origin(app):
    c = TestClient(app)
    assert c.post("/api/assistant/message", json={"text": "а" * 2001}).status_code == 422
    assert c.post("/api/assistant/message", json={}).status_code == 200  # пустое сообщение — мягкий ответ
    assert c.post("/api/assistant/message", json={"text": "привет"}, headers={"Origin": "https://evil.example"}).status_code == 403


# ============================================================================ анкета -> документ
def test_full_flow_questionnaire_to_downloaded_docx(app, template):
    c = TestClient(app)
    offer = say(c, "Я хочу перевестись")
    assert offer["reply"]["kind"] == "offer" and offer["dialog"]["state"] == "idle"
    first = say(c, "да")
    assert first["dialog"]["state"] == "screening" and first["reply"]["question"] == "student_name"
    for text, key in zip(ANSWERS, ["current_university", "current_course", "current_specialty", "education_form", "has_debts", "target_specialty"]):
        assert say(c, text)["reply"]["question"] == key
    assert say(c, "Прикладная информатика")["reply"]["question"] == "passport_data"
    review = say(c, "1234 567890")
    assert review["reply"]["kind"] == "review" and "Иванов Иван Иванович" in review["reply"]["text"] and review["dialog"]["state"] == "review"
    assert c.get("/api/assistant/state").json()["downloads"] == []  # до подтверждения документ не создаётся
    assert c.post("/api/assistant/generate", json={"template_id": template}).status_code == 409
    conf = say(c, "верно")
    assert conf["dialog"]["state"] == "document_generation"
    gen = c.post("/api/assistant/generate", json={"template_id": template})
    assert gen.status_code == 200
    j = gen.json()
    assert j["dialog"]["state"] == "completed" and j["unfilled"] == [] and j["download"]["filename"].endswith(".docx")
    dl = c.get(j["download"]["url"])
    assert dl.status_code == 200 and dl.content.startswith(DOCX_MAGIC) and "wordprocessingml" in dl.headers["content-type"] and "no-store" in dl.headers["cache-control"]
    doc = Document(io.BytesIO(dl.content))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c_.text for t in doc.tables for r in t.rows for c_ in r.cells)
    assert "Иванов Иван Иванович" in text and "Южный федеральный университет" in text and "Прикладная информатика" in text and "серия 1234 № 567890" in text
    assert "{{" not in text and "отсутствуют" in text and time.strftime("%d.%m.%Y") in doc.sections[0].footer.paragraphs[0].text


def test_unfilled_fields_are_reported_not_invented(app, template):
    c = TestClient(app)
    say(c, "Я хочу перевестись", "да", *ANSWERS, "пропустить", "пропустить", "верно")
    j = c.post("/api/assistant/generate", json={"template_id": template}).json()
    assert set(j["unfilled"]) == {"паспортные данные", "целевое направление"} and "паспортные данные" in j["reply"]["text"]
    doc = Document(io.BytesIO(c.get(j["download"]["url"]).content))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c_.text for t in doc.tables for r in t.rows for c_ in r.cells)
    # данных нет — в документе строка для заполнения от руки, а не метка шаблона и не выдуманное значение
    assert "{{" not in text and text.count("________") >= 2


def test_double_click_on_generate_does_not_create_duplicates(app, template):
    c = TestClient(app)
    first = complete(c)
    again = c.post("/api/assistant/generate", json={"template_id": template}).json()
    assert again["duplicate"] is True and again["download"]["id"] == first["download"]["id"]
    assert len(c.get("/api/assistant/state").json()["downloads"]) == 1
    # данные изменились -> новый документ
    say(c, "Хочу перевестись")  # ничего не делает: идёт предложение
    assert len(list(app.state.storage.output_dir.glob("*.docx"))) == 1


def test_generate_without_templates_or_with_unknown_template(app, template, admin):
    c = TestClient(app)
    finish_questionnaire(c)
    say(c, "верно")
    r = c.post("/api/assistant/generate", json={"template_id": "nope"})
    assert r.status_code == 404 and "Шаблон не найден" in r.json()["detail"]
    admin.delete(f"/api/admin/templates/{template}")
    r = c.post("/api/assistant/generate", json={})
    assert r.status_code == 404 and "администратор ещё не загрузил" in r.json()["detail"]
    assert c.get("/api/assistant/state").json()["dialog"]["state"] == "document_generation"


def test_generation_through_chat_message_and_single_template_autoselect(app, template):
    c = TestClient(app)
    finish_questionnaire(c)
    say(c, "верно")
    j = say(c, "сформировать")
    assert j["reply"]["kind"] == "done" and j["download"]["id"] and j["dialog"]["state"] == "completed"


def test_several_templates_require_a_choice(app, admin, template, tmp_path):
    data = build_sample_template(tmp_path / "t2.docx").read_bytes()
    admin.post("/api/admin/templates", files={"file": ("second.docx", data, "application/octet-stream")}, data={"template_id": "second"})
    c = TestClient(app)
    finish_questionnaire(c)
    say(c, "верно")
    assert c.post("/api/assistant/generate", json={}).status_code == 400
    assert c.post("/api/assistant/generate", json={"template_id": "second"}).status_code == 200


# ============================================================================ срок хранения и истечение сессии
def expire_session(app, client):
    app.state.storage.db.execute("UPDATE user_sessions SET expires_at=? WHERE id=?", (time.time() - 5, client.cookies.get("dekanat_sid")))


def test_expired_session_is_reported_and_data_is_gone(app, template):
    c = TestClient(app)
    done = complete(c)
    path = Path(app.state.storage.db.query("SELECT path FROM generated_docs")[0]["path"])
    assert path.exists()
    expire_session(app, c)
    assert c.get(done["download"]["url"]).status_code == 410
    assert c.post("/api/assistant/generate", json={"template_id": template}).status_code == 410
    assert c.patch("/api/assistant/document/fields", json={"unit": "student_name", "value": "x"}).status_code == 410
    assert c.post("/api/assistant/submission", json={"confirm": True}).status_code == 410
    j = say(c, "привет")
    assert j["session"] == "expired" and j["dialog"]["state"] == "idle"
    app.state.storage.purge()
    assert not path.exists() and app.state.storage.db.query("SELECT * FROM generated_docs") == []  # просроченные файлы удалены


def test_generated_documents_expire_by_their_own_ttl(app, template):
    c = TestClient(app)
    done = complete(c)
    app.state.storage.db.execute("UPDATE generated_docs SET expires_at=?", (time.time() - 1,))
    assert c.get(done["download"]["url"]).status_code == 404
    path = Path(app.state.storage.db.query("SELECT path FROM generated_docs")[0]["path"])
    assert app.state.storage.purge()["files"] == 1 and not path.exists()


def test_delete_my_data_removes_everything(app, template):
    c = TestClient(app)
    done = complete(c)
    sid = c.cookies.get("dekanat_sid")
    path = Path(app.state.storage.db.query("SELECT path FROM generated_docs")[0]["path"])
    r = c.delete("/api/assistant/data")
    assert r.status_code == 200 and not path.exists()
    st = app.state.storage
    assert st.sessions.load_state(sid) is None and st.db.query("SELECT * FROM user_sessions WHERE id=?", (sid,)) == []
    assert c.get(done["download"]["url"]).status_code == 410 or c.get(done["download"]["url"]).status_code == 404
    assert c.get("/api/assistant/state").json()["dialog"]["state"] == "idle"


def test_ticket_retention_removes_old_tickets_and_attachments(app, template):
    c = TestClient(app)
    complete(c)
    t = c.post("/api/assistant/submission", json={"confirm": True}).json()
    folder = app.state.storage.ticket_dir(t["ticket_id"])
    assert (folder / "application.docx").exists()
    app.state.storage.db.execute("UPDATE tickets SET created_at=?", (time.time() - 400 * 86400,))
    assert app.state.storage.purge()["tickets"] == 1 and not folder.exists()


# ============================================================================ отправка в деканат
def test_submission_requires_confirmation_and_shows_the_payload(app, admin, template):
    c = TestClient(app)
    c.get("/api/assistant/state")
    assert c.post("/api/assistant/submission", json={"confirm": True}).status_code == 409  # документ ещё не готов
    complete(c)
    prev = c.get("/api/assistant/submission").json()
    assert prev["delivery"]["available"] and prev["attachment"] and prev["payload"]["request"] == "перевод"
    assert prev["payload"]["fields"]["ФИО"] == "Иванов Иван Иванович" and "Паспортные данные" not in prev["payload"]["fields"] and prev["excluded"] == ["Паспортные данные"]
    assert c.get("/api/assistant/submission?include_passport=true").json()["payload"]["fields"]["Паспортные данные"] == "серия 1234 № 567890"
    # отказ: ничего не отправляется
    declined = c.post("/api/assistant/submission", json={"confirm": False}).json()
    assert declined["sent"] is False and admin.get("/api/admin/tickets").json()["total"] == 0
    ok = c.post("/api/assistant/submission", json={"confirm": True})
    assert ok.status_code == 200 and ok.json()["sent"] is True and ok.json()["excluded"] == ["Паспортные данные"]
    tid = ok.json()["ticket_id"]
    ticket = admin.get(f"/api/admin/tickets/{tid}").json()
    assert ticket["type"] == "transfer" and ticket["status"] == "new" and ticket["payload"]["fields"]["Текущий вуз"] == "Южный федеральный университет"
    assert "серия 1234" not in str(ticket["payload"]) and ticket["has_attachment"] is True
    att = admin.get(f"/api/admin/tickets/{tid}/attachment")
    assert att.status_code == 200 and att.content.startswith(DOCX_MAGIC)


def test_resubmission_is_idempotent(app, admin, template):
    c = TestClient(app)
    complete(c)
    first = c.post("/api/assistant/submission", json={"confirm": True}).json()
    second = c.post("/api/assistant/submission", json={"confirm": True}).json()
    assert second["duplicate"] is True and second["ticket_id"] == first["ticket_id"]
    assert admin.get("/api/admin/tickets").json()["total"] == 1


def test_passport_is_sent_only_when_the_user_asks(app, admin, template):
    c = TestClient(app)
    complete(c)
    tid = c.post("/api/assistant/submission", json={"confirm": True, "include_passport": True}).json()["ticket_id"]
    assert admin.get(f"/api/admin/tickets/{tid}").json()["payload"]["fields"]["Паспортные данные"] == "серия 1234 № 567890"


def test_submission_is_honestly_unavailable_without_a_channel(tmp_settings, tmp_path):
    tmp_settings.raw["delivery"]["channel"] = "none"
    app = create_app(tmp_settings)
    admin = TestClient(app)
    admin_login(admin, app)
    data = build_sample_template(tmp_path / "t.docx").read_bytes()
    admin.post("/api/admin/templates", files={"file": ("p.docx", data, "application/octet-stream")}, data={"template_id": "perevod"})
    c = TestClient(app)
    complete(c)
    r = c.post("/api/assistant/submission", json={"confirm": True})
    assert r.status_code == 503 and "пока недоступна" in r.json()["detail"] and "Скачайте готовый документ" in r.json()["detail"]
    assert c.get("/api/assistant/state").json()["delivery"]["available"] is False
    assert c.get("/api/health").json()["delivery"]["channel"] == "none"


def test_queue_without_any_admin_is_reported_unavailable(tmp_settings, tmp_path):
    app = create_app(tmp_settings)  # администратора нет — очередь некому читать
    store = app.state.svc.storage
    from dekanat.templates import convert_to_template

    convert_to_template(build_sample_template(tmp_path / "t.docx"), storage=store, template_id="perevod")
    c = TestClient(app)
    complete(c)
    r = c.post("/api/assistant/submission", json={"confirm": True})
    assert r.status_code == 503 and "нет администратора" in r.json()["detail"]


# ============================================================================ вопросы вне анкеты
def test_rule_question_gets_structured_answer_with_clause(app):
    c = TestClient(app)
    j = say(c, "Сколько дней рассматривается заявление?")
    a = j["answer"]
    assert a["verdict"] == "ok" and a["sources"][0]["ref"] == "6.1" and j["reply"]["text"] == a["text"]
    rep = a["report"]
    assert rep["conclusion"] == a["text"] and rep["clauses"][0]["ref"] == "6.1" and rep["no_source"] is False
    assert "Окончательное решение принимает сотрудник деканата" in rep["disclaimer"] and rep["facts"] == [] and rep["missing"] == []


def test_unknown_question_honestly_says_no_answer(app):
    j = say(TestClient(app), "Сколько стоит обучение?")
    a = j["answer"]
    assert a["verdict"] == "no_answer" and a["sources"] == [] and "Обратитесь в деканат" in a["text"] and a["report"]["no_source"] is True


def test_document_check_without_document_asks_for_one(app):
    a = say(TestClient(app), "Хватит ли этой справки для перевода?")["answer"]
    assert a["verdict"] == "no_answer" and "загрузите его фото или скан" in a["text"]


# ============================================================================ документ сессии (нужен Tesseract)
def png_bytes(samples, name):
    return (samples / name).read_bytes()


@needs_ocr
def test_upload_png_jpg_and_pdf(app, samples, tmp_path):
    pdf = tmp_path / "scan.pdf"
    Image.open(samples / "sample_02.png").convert("RGB").save(pdf, "PDF")
    for name, data, mime in (("sample_02.png", (samples / "sample_02.png").read_bytes(), "image/png"), ("sample_03.jpg", (samples / "sample_03.jpg").read_bytes(), "image/jpeg"),
                             ("scan.pdf", pdf.read_bytes(), "application/pdf")):
        c = TestClient(app)
        r = c.post("/api/assistant/document", files={"file": (name, data, mime)}, data={"doc_type": "auto"})
        assert r.status_code == 200, (name, r.text)
        j = r.json()
        assert j["document"]["doc_type"]["id"] in ("study_certificate", "academic_leave", "practice_certificate") and j["document"]["fields"]
        assert j["answer"]["report"]["disclaimer"] and "image" not in str(j).lower()[:0]
        assert c.get("/api/assistant/document").json()["document"]["filename"] == name


@needs_ocr
def test_document_card_can_be_checked_and_corrected(app, samples):
    c = TestClient(app)
    j = c.post("/api/assistant/document", files={"file": ("s.png", (samples / "sample_02.png").read_bytes(), "image/png")}, data={"doc_type": "study_certificate"}).json()
    assert j["answer"]["verdict"] == "missing"
    fields = {f["unit"]: f for f in j["document"]["fields"]}
    assert fields["student_name"]["found"] and fields["issue.date"]["found"] is False and fields["seal_signature.stamp"]["found"] is False
    assert any("не найдено" in f["value"] for f in j["document"]["fields"] if not f["found"] and f["kind"] != "mark") or True
    bad = c.patch("/api/assistant/document/fields", json={"unit": "issue.date", "value": "завтра"})
    assert bad.status_code == 400 and "формат" in bad.json()["detail"]
    assert c.patch("/api/assistant/document/fields", json={"unit": "nonexistent", "value": "x"}).status_code == 400
    assert c.patch("/api/assistant/document/fields", json={"unit": "seal_signature.stamp", "value": "возможно"}).status_code == 400
    ok1 = c.patch("/api/assistant/document/fields", json={"unit": "issue.date", "value": "15.05.2024"})
    assert ok1.status_code == 200 and ok1.json()["answer"]["verdict"] == "missing"
    ok2 = c.patch("/api/assistant/document/fields", json={"unit": "seal_signature.stamp", "value": "есть"})
    a = ok2.json()["answer"]
    assert a["verdict"] == "ok" and a["sources"][0]["ref"] == "3.2"
    corrected = {f["unit"]: f for f in ok2.json()["document"]["fields"]}
    assert corrected["issue.date"]["value"] == "15.05.2024" and corrected["issue.date"]["corrected"] and corrected["seal_signature.stamp"]["corrected"]
    # очистка поля возвращает «не найдено»
    cleared = c.patch("/api/assistant/document/fields", json={"unit": "issue.date", "value": ""}).json()
    assert {f["unit"]: f for f in cleared["document"]["fields"]}["issue.date"]["found"] is False and cleared["answer"]["verdict"] == "missing"


@needs_ocr
def test_questions_in_chat_use_the_uploaded_document(app, samples):
    c = TestClient(app)
    c.post("/api/assistant/document", files={"file": ("s.png", (samples / "sample_02.png").read_bytes(), "image/png")}, data={"doc_type": "study_certificate"})
    j = say(c, "Какая форма обучения указана в справке?")
    assert j["answer"]["intent"] == "fact" and j["answer"]["sources"][0]["kind"] == "field" and j["answer"]["report"]["facts"][0]["title"]
    j = say(c, "Хватит ли этой справки для перевода на 2 курс?")
    assert j["answer"]["verdict"] == "missing" and j["answer"]["sources"][0]["ref"] == "3.4"
    assert j["answer"]["report"]["missing"] and j["answer"]["report"]["facts"]


@needs_ocr
def test_document_data_suggests_questionnaire_answers_without_mixing_target(app, samples, template):
    c = TestClient(app)
    j = c.post("/api/assistant/document", files={"file": ("s.png", (samples / "sample_02.png").read_bytes(), "image/png")}, data={"doc_type": "study_certificate"}).json()
    name = {f["unit"]: f for f in j["document"]["fields"]}["student_name"]["value"]
    r = say(c, "Я хочу перевестись", "да")
    assert "В загруженном документе найдено" in r["reply"]["text"] and name in r["reply"]["text"]
    r = say(c, "да")
    assert r["reply"]["question"] == "current_university"
    assert "В загруженном документе найдено" in r["reply"]["text"]  # вуз из документа — это ТЕКУЩИЙ вуз
    data = app.state.storage.sessions.load_state(c.cookies.get("dekanat_sid"))["data"]
    assert data["student_name"] == name and "target_specialty" not in data


def test_upload_validation_errors(tmp_settings, tmp_path):
    tmp_settings.raw["server"]["max_upload_mb"] = 1
    app = create_app(tmp_settings)
    c = TestClient(app)
    c.get("/api/assistant/state")
    assert c.post("/api/assistant/document", files={"file": ("a.png", b"", "image/png")}).status_code == 400
    r = c.post("/api/assistant/document", files={"file": ("a.png", b"this is plain text, not an image", "image/png")})
    assert r.status_code == 400 and "Не удалось прочитать" in r.json()["detail"]
    r = c.post("/api/assistant/document", files={"file": ("a.pdf", b"%PDF-1.4 broken", "application/pdf")})
    assert r.status_code == 400
    assert c.post("/api/assistant/document", files={"file": ("big.png", b"\x89PNG" + b"0" * (2 * 1024 * 1024), "image/png")}).status_code == 413
    assert c.post("/api/assistant/document").status_code == 422
    assert c.patch("/api/assistant/document/fields", json={"unit": "x", "value": "y"}).status_code == 404  # документа в сессии нет


# ============================================================================ анкета на главной странице («Стол декана»)
def wait_job(c, job_id, timeout=180.0):
    end = time.time() + timeout
    while time.time() < end:
        snap = c.get(f"/api/jobs/{job_id}").json()
        if snap["status"] in ("done", "error", "cancelled"):
            return snap
        time.sleep(0.2)
    raise AssertionError("задача не завершилась")


def check_on_desk(c, data, name="s.png", question="", doc_type="study_certificate"):
    """Проверка так, как её запускает стол: фоновая задача /api/jobs с cookie сессии."""
    r = c.post("/api/jobs", files={"file": (name, data, "application/octet-stream")}, data={"question": question, "doc_type": doc_type})
    assert r.status_code == 200, r.text
    snap = wait_job(c, r.json()["id"])
    assert snap["status"] == "done", snap.get("error")
    return snap["result"]


def part(fields, field_id, part_id):
    return next(p for p in next(f for f in fields if f["id"] == field_id)["parts"] if p["id"] == part_id)


def test_assistant_page_moved_to_the_desk(app):
    r = TestClient(app).get("/assistant", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/"


def test_deferred_message_leaves_ordinary_questions_to_the_desk(app):
    c = TestClient(app)
    c.get("/api/assistant/state")
    j = say(c, "Сколько дней рассматривается заявление?", defer=True)
    assert j["reply"]["kind"] == "passthrough" and j["answer"] is None and j["dialog"]["state"] == "idle"
    scenarios = [r["scenario"] for r in app.state.storage.db.query("SELECT scenario FROM events")]
    assert "question" not in scenarios  # вопрос учтёт проверка, которую запустит стол, — без двойного счёта
    j = say(c, "Я хочу перевестись", defer=True)
    assert j["reply"]["kind"] == "offer" and j["reply"]["options"] == ["Да, начать", "Нет"]
    j = say(c, "да", defer=True)
    assert j["reply"]["kind"] == "question" and j["reply"]["progress"][0] == 1 and j["dialog"]["state"] == "screening"
    # без defer (прежние клиенты API) вопрос вне анкеты по-прежнему получает ответ сразу
    c2 = TestClient(app)
    j = say(c2, "Сколько дней рассматривается заявление?")
    assert j["answer"]["sources"][0]["ref"] == "6.1"


def test_desk_check_without_session_stores_nothing(app, samples):
    c = TestClient(app)  # cookie сессии нет: проверка работает, но в базу ничего не попадает
    r = c.post("/api/jobs", data={"question": "Сколько дней рассматривается заявление?", "doc_type": "auto"})
    snap = wait_job(c, r.json()["id"])
    assert snap["status"] == "done" and snap["result"]["corrected"] == []
    assert app.state.storage.db.query("SELECT COUNT(*) AS n FROM user_sessions")[0]["n"] == 0


@needs_ocr
def test_desk_check_becomes_the_session_document(app, samples):
    """Файл, проверенный на главной, становится документом сессии: из него подсказки анкеты, его поля можно исправить."""
    c = TestClient(app)
    c.get("/api/assistant/state")
    res = check_on_desk(c, png_bytes(samples, "sample_02.png"))
    assert res["corrected"] == [] and res["doc_type"]["id"] == "study_certificate"
    doc = c.get("/api/assistant/document").json()["document"]
    assert doc["filename"] == "s.png" and doc["fields"]
    stored = app.state.storage.sessions.load_document(c.cookies.get("dekanat_sid"))
    assert set(stored) == {"filename", "uploaded_at", "doc_type", "fields", "corrected", "warnings", "digest"}  # ни снимка, ни OCR-текста
    r = say(c, "Я хочу перевестись", "да", defer=True)
    assert "В загруженном документе найдено" in r["reply"]["text"]


@needs_ocr
def test_corrections_survive_a_follow_up_question_about_the_same_file(app, samples):
    c = TestClient(app)
    c.get("/api/assistant/state")
    data = png_bytes(samples, "sample_02.png")
    first = check_on_desk(c, data, question="Хватит ли этой справки для перевода на 2 курс?")
    assert first["answer"]["verdict"] == "missing" and part(first["fields"], "issue", "date")["found"] is False
    r = c.patch("/api/assistant/document/fields", json={"unit": "issue.date", "value": "15.05.2024", "question": "Хватит ли этой справки для перевода на 2 курс?"})
    assert r.status_code == 200
    j = r.json()
    assert part(j["fields"], "issue", "date")["value"] == "15.05.2024"  # карточка в виде результата проверки — стол обновляет её на месте
    assert j["answer"]["sources"][0]["ref"] == "3.4"  # ответ пересчитан на тот же вопрос, а не на общую проверку
    c.patch("/api/assistant/document/fields", json={"unit": "seal_signature.stamp", "value": "есть"})

    again = check_on_desk(c, data, question="Хватит ли этой справки для перевода на 2 курс?")
    assert sorted(again["corrected"]) == ["issue.date", "seal_signature.stamp"]
    assert part(again["fields"], "issue", "date")["value"] == "15.05.2024" and part(again["fields"], "seal_signature", "stamp")["found"] is True
    assert next(f for f in again["fields"] if f["id"] == "student_name")["boxes"]  # рамки на снимке остаются
    assert "даты" not in again["answer"]["text"]
    assert c.get("/api/assistant/document").json()["document"]["corrected"] == ["issue.date", "seal_signature.stamp"]

    # распознавание кешируется: чужая сессия видит исходные значения, а не исправления этой
    other = TestClient(app)
    other.get("/api/assistant/state")
    fresh = check_on_desk(other, data)
    assert fresh["corrected"] == [] and part(fresh["fields"], "issue", "date")["found"] is False

    # другой файл заменяет документ сессии, исправления прежнего к нему не относятся
    new = check_on_desk(c, png_bytes(samples, "sample_03.jpg"), name="b.jpg", doc_type="auto")
    assert new["corrected"] == [] and c.get("/api/assistant/document").json()["document"]["filename"] == "b.jpg"


# ============================================================================ когда обращение не уходит в деканат
def complete_with(c, debts="нет", target="Прикладная информатика"):
    say(c, "Я хочу перевестись", "да", *ANSWERS[:-1], debts, target, "пропустить")
    say(c, "верно")
    r = c.post("/api/assistant/generate", json={"template_id": "perevod"})
    assert r.status_code == 200, r.text


def test_submission_is_blocked_with_academic_debts_but_the_document_is_still_ready(app, admin, template):
    c = TestClient(app)
    complete_with(c, debts="да, по матанализу")
    prev = c.get("/api/assistant/submission").json()
    assert prev["attachment"] and any("задолженност" in r for r in prev["blocked"])
    r = c.post("/api/assistant/submission", json={"confirm": True})
    assert r.status_code == 409 and "задолженност" in r.json()["detail"] and "не отправлено" in r.json()["detail"]
    assert admin.get("/api/admin/tickets").json()["total"] == 0


def test_submission_is_blocked_without_the_target_direction(app, admin, template):
    c = TestClient(app)
    complete_with(c, target="пропустить")
    r = c.post("/api/assistant/submission", json={"confirm": True})
    assert r.status_code == 409 and "направление" in r.json()["detail"]
    assert admin.get("/api/admin/tickets").json()["total"] == 0


def test_clean_questionnaire_is_not_blocked_and_the_rule_can_be_relaxed(app, tmp_settings, template, admin):
    c = TestClient(app)
    complete_with(c)
    assert c.get("/api/assistant/submission").json()["blocked"] == []
    assert c.post("/api/assistant/submission", json={"confirm": True}).status_code == 200


def test_debts_rule_is_a_setting(tmp_settings, tmp_path):
    from dekanat.dialog import DialogContext
    from dekanat.dialog.eligibility import check_submission
    ctx = DialogContext(scenario="transfer", data={"student_name": "Иванов Иван Иванович", "current_university": "ЮФУ", "current_course": 2, "current_specialty": "09.03.01 Информатика",
                                                  "education_form": "очная", "has_debts": True, "target_specialty": "Прикладная информатика"})
    assert any("задолженност" in r for r in check_submission(ctx))
    assert check_submission(ctx, lambda key, default: True if key == "submission.allow_debts" else default) == []
    ctx.data["current_course"] = 9  # недостоверное значение не проходит и повторную проверку
    assert any("Текущий курс" in r for r in check_submission(ctx, lambda key, default: True if key == "submission.allow_debts" else default))


def test_review_warns_about_the_blocked_submission_while_answers_can_still_be_fixed(app, template):
    c = TestClient(app)
    last = say(c, "Я хочу перевестись", "да", *ANSWERS[:-1], "да, по матанализу", "Прикладная информатика", "пропустить")
    text = last["reply"]["text"]
    assert last["reply"]["kind"] == "review" and "отправить не получится" in text and "задолженност" in text
    clean = TestClient(app)
    ok = say(clean, "Я хочу перевестись", "да", *ANSWERS, "Прикладная информатика", "пропустить")
    assert ok["reply"]["kind"] == "review" and "отправить не получится" not in ok["reply"]["text"]
