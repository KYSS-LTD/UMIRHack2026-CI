"""Письмо с номером обращения и кодом статуса: только по желанию, только если почта настроена, без данных анкеты; сбой почты не ломает обращение."""

import smtplib

import pytest
from fastapi.testclient import TestClient

from dekanat.mailer import DISABLED, FAILED, SENT, Mailer, mask_email
from dekanat.web.app import create_app
from test_assistant import ANSWERS, say


class FakeSMTP:
    """Подставной почтовый сервер: запоминает всё, что ему отправили."""

    sent: list = []
    log: list = []
    fail = False

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail:
            raise OSError("connection refused")
        FakeSMTP.log.append(("connect", host, port))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        FakeSMTP.log.append(("starttls",))

    def login(self, user, password):
        FakeSMTP.log.append(("login", user, password))

    def send_message(self, msg):
        FakeSMTP.sent.append(msg)


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.sent, FakeSMTP.log, FakeSMTP.fail = [], [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setenv("DEKANAT_MAIL_PASSWORD", "secret-from-env")


def configure(settings, **over):
    settings.raw["mail"] = {"enabled": True, "host": "smtp.example.ru", "port": 587, "security": "starttls", "username": "dekanat@example.ru",
                            "from": "dekanat@example.ru", "public_url": "https://dekanat.example.ru", **over}
    return settings


def student_with_ticket(app, tmp_path, contact):
    from conftest import admin_login  # noqa: F401  (шаблон загружает администратор)
    from dekanat.templates.sample import build_sample_template

    admin = TestClient(app)
    admin_login(admin, app)
    admin.post("/api/admin/templates", files={"file": ("p.docx", build_sample_template(tmp_path / "t.docx").read_bytes(), "application/octet-stream")}, data={"template_id": "perevod"})
    c = TestClient(app)
    say(c, "Я хочу перевестись", "да", *ANSWERS, "Прикладная информатика", "1234 567890", "верно")
    assert c.post("/api/assistant/generate", json={"template_id": "perevod"}).status_code == 200
    return c, c.post("/api/assistant/submission", json={"confirm": True, "contact": contact})


def test_mask_email():
    assert mask_email("ivan@example.com") == "i***@example.com" and mask_email("broken") == "***"


def test_mailer_status_and_disabled_by_default(tmp_settings):
    assert Mailer(tmp_settings).status() == {"available": False, "reason": "почта не настроена (mail.enabled)"}
    assert Mailer(tmp_settings).send_ticket_code("a@b.ru", 1, "AAAA-BBBB") == DISABLED
    configure(tmp_settings, host="")
    assert not Mailer(tmp_settings).status()["available"] and "mail.host" in Mailer(tmp_settings).status()["reason"]
    assert FakeSMTP.sent == []


def test_the_code_goes_to_the_students_email_without_personal_data(tmp_settings, tmp_path):
    app = create_app(configure(tmp_settings))
    c, r = student_with_ticket(app, tmp_path, "ivan@example.com")
    j = r.json()
    assert r.status_code == 200 and j["sent"] and j["mail"] == {"status": SENT, "to": "i***@example.com"}
    assert len(FakeSMTP.sent) == 1
    m = FakeSMTP.sent[0]
    body = m.get_content()
    assert m["To"] == "ivan@example.com" and m["From"] == "dekanat@example.ru" and str(j["ticket_id"]) in m["Subject"]
    assert j["code"] in body and f"https://dekanat.example.ru/#/status/{j['ticket_id']}" in body
    assert "Иванов" not in body and "1234" not in body and "перевод" not in body.lower().replace("обращение", "")  # ни ФИО, ни паспорта, ни данных анкеты
    assert ("starttls",) in FakeSMTP.log and ("login", "dekanat@example.ru", "secret-from-env") in FakeSMTP.log  # пароль — только из окружения
    # повторное нажатие в той же сессии не шлёт второе письмо
    again = c.post("/api/assistant/submission", json={"confirm": True, "contact": "ivan@example.com"}).json()
    assert again["duplicate"] and len(FakeSMTP.sent) == 1
    assert c.get("/api/assistant/submission").json()["mail"]["available"] is True


def test_unconfigured_mail_is_reported_honestly_and_the_ticket_still_goes(tmp_settings, tmp_path):
    app = create_app(tmp_settings)
    c, r = student_with_ticket(app, tmp_path, "ivan@example.com")
    j = r.json()
    assert r.status_code == 200 and j["sent"] and j["code"] and j["mail"]["status"] == "disabled" and FakeSMTP.sent == []


@pytest.mark.parametrize("contact, status", [("", "none"), ("+7 (900) 123-45-67", "skipped"), ("@ivan_ivanov", "skipped")])
def test_no_letter_without_an_email(tmp_settings, tmp_path, contact, status):
    app = create_app(configure(tmp_settings))
    _, r = student_with_ticket(app, tmp_path, contact)
    assert r.json()["mail"]["status"] == status and FakeSMTP.sent == []


def test_a_failing_mail_server_does_not_break_the_request(tmp_settings, tmp_path):
    FakeSMTP.fail = True
    app = create_app(configure(tmp_settings))
    c, r = student_with_ticket(app, tmp_path, "ivan@example.com")
    j = r.json()
    assert r.status_code == 200 and j["sent"] and j["code"] and j["mail"]["status"] == FAILED
    FakeSMTP.fail = False
    assert Mailer(tmp_settings).send_ticket_code("a@b.ru", 1, "AAAA-BBBB") == SENT


def test_without_public_url_the_letter_points_to_the_menu(tmp_settings):
    m = Mailer(configure(tmp_settings, public_url=""))
    assert m.send_ticket_code("a@b.ru", 5, "AAAA-BBBB") == SENT
    assert "меню" in FakeSMTP.sent[0].get_content() and "http" not in FakeSMTP.sent[0].get_content()
