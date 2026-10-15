"""HTTP-интерфейс аккаунтов: вход через бота, вход из Mini App, профиль, обращения, синхронизация сайта и бота, webhook. Telegram не нужен."""

import hashlib
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import admin_login
from dekanat.templates.sample import build_sample_template
from dekanat.web.app import create_app
from test_assistant import ANSWERS
from test_telegram_auth import TOKEN, sign_webapp

BOT = "dekanat_test_bot"
USER = {"id": 777, "first_name": "Мария", "last_name": "Орлова", "username": "maria_o", "language_code": "ru"}


def prefix_of(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:24]


def tg_settings(tmp_settings, **extra):
    tmp_settings.raw["telegram"].update(enabled=True, bot_token=TOKEN, bot_username=BOT, public_url="https://dekanat.test", **extra)
    return tmp_settings


@pytest.fixture()
def app(tmp_settings):
    return create_app(tg_settings(tmp_settings))


@pytest.fixture()
def client(app):
    return TestClient(app, base_url="https://dekanat.test")


@pytest.fixture()
def plain(tmp_path_factory):
    """Приложение с выключенным Telegram — как по умолчанию."""
    from conftest import make_settings

    return TestClient(create_app(make_settings(tmp_path_factory.mktemp("plain"))))


def sign_in_via_bot(client, app, profile=None):
    """Вход так, как он происходит на деле: сайт просит ссылку, человек подтверждает в боте, сайт опрашивает."""
    r = client.post("/api/auth/telegram/start")
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert app.state.accounts.answer_login(prefix_of(token), profile or {"id": USER["id"], "username": USER["username"], "first_name": USER["first_name"],
                                                                         "last_name": USER["last_name"], "language": "ru"}, True) == "ok"
    p = client.get("/api/auth/telegram/poll", params={"token": token})
    assert p.json()["status"] == "confirmed", p.text
    return p.json()


# ------------------------------------------------------------------ без Telegram
def test_default_install_has_no_telegram(plain):
    me = plain.get("/api/account/me").json()
    assert me == {"user": None, "telegram": {"enabled": False, "login": False, "bot": None, "mini_app": False}}
    assert plain.get("/api/health").json()["telegram"]["enabled"] is False
    assert plain.post("/api/auth/telegram/start").status_code == 404
    assert plain.post("/api/auth/telegram/webapp", json={"init_data": "x"}).status_code == 404
    assert plain.get("/", follow_redirects=False).headers["x-frame-options"] == "DENY"


def test_site_works_without_account(plain):
    """ТЗ: работа через браузерную сессию без регистрации."""
    r = plain.get("/api/assistant/state")
    assert r.status_code == 200 and r.json()["session"] == "new"
    assert plain.post("/api/assistant/message", json={"text": "Я хочу перевестись"}).status_code == 200
    assert plain.get("/api/account/tickets").status_code == 401  # аккаунтные методы требуют входа, остальное — нет


# ------------------------------------------------------------------ вход через бота
def test_me_for_anonymous_does_not_create_session(client):
    r = client.get("/api/account/me")
    assert r.json()["user"] is None and r.json()["telegram"] == {"enabled": True, "login": True, "bot": BOT, "mini_app": True}
    assert "set-cookie" not in r.headers


def test_login_links_and_polling(client, app):
    r = client.post("/api/auth/telegram/start").json()
    assert r["link"] == f"https://t.me/{BOT}?start=login_{r['token']}" and r["expires_in"] == 300
    assert client.get("/api/auth/telegram/poll", params={"token": r["token"]}).json() == {"status": "pending"}
    assert "dekanat_sid" in client.cookies


def test_login_end_to_end_rotates_cookie(client, app):
    before = client.post("/api/auth/telegram/start")
    old_cookie = client.cookies.get("dekanat_sid")
    token = before.json()["token"]
    app.state.accounts.answer_login(prefix_of(token), {"id": 777, "username": "maria_o", "first_name": "Мария", "last_name": "Орлова"}, True)
    done = client.get("/api/auth/telegram/poll", params={"token": token}).json()
    assert done["status"] == "confirmed" and done["user"]["name"] == "Мария Орлова"
    assert "tg_id" not in json.dumps(done)  # Telegram-идентификатор браузеру не нужен
    assert client.cookies.get("dekanat_sid") != old_cookie
    assert client.get("/api/account/me").json()["user"]["username"] == "maria_o"
    assert client.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "expired"  # один раз


def test_old_cookie_does_not_survive_login(client, app, tmp_settings):
    client.get("/api/assistant/state")
    stolen = client.cookies.get("dekanat_sid")
    sign_in_via_bot(client, app)
    thief = TestClient(app, base_url="https://dekanat.test")
    thief.cookies.set("dekanat_sid", stolen)
    assert thief.get("/api/account/me").json()["user"] is None  # идентификатор, известный до входа, после входа бесполезен


def test_login_token_cannot_be_taken_by_another_browser(client, app):
    token = client.post("/api/auth/telegram/start").json()["token"]
    app.state.accounts.answer_login(prefix_of(token), {"id": 777}, True)
    other = TestClient(app, base_url="https://dekanat.test")
    other.get("/api/assistant/state")
    assert other.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "expired"
    assert client.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "confirmed"


def test_denied_login(client, app):
    token = client.post("/api/auth/telegram/start").json()["token"]
    app.state.accounts.answer_login(prefix_of(token), {"id": 777}, False)
    assert client.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "denied"
    assert client.get("/api/account/me").json()["user"] is None


def test_start_when_already_signed_in(client, app):
    sign_in_via_bot(client, app)
    assert client.post("/api/auth/telegram/start").json()["already"] is True


def test_login_requires_running_bot(tmp_settings):
    tmp_settings.raw["telegram"].update(enabled=True, bot_token=TOKEN, bot_username=None)
    c = TestClient(create_app(tmp_settings))
    assert c.get("/api/account/me").json()["telegram"]["login"] is False
    assert c.post("/api/auth/telegram/start").status_code == 503


# ------------------------------------------------------------------ Mini App
def test_webapp_login(client):
    r = client.post("/api/auth/telegram/webapp", json={"init_data": sign_webapp(USER)})
    assert r.status_code == 200 and r.json()["user"]["name"] == "Мария Орлова"
    assert client.get("/api/account/me").json()["user"]["linked_web"] is True


def test_webapp_rejects_forged_and_stale_data(client):
    assert client.post("/api/auth/telegram/webapp", json={"init_data": sign_webapp(USER, token="1:OTHER")}).status_code == 401
    assert client.post("/api/auth/telegram/webapp", json={"init_data": sign_webapp(USER, auth_date=time.time() - 5 * 86400)}).status_code == 401
    assert client.get("/api/account/me").json()["user"] is None


def test_webapp_failures_are_rate_limited(client):
    for _ in range(10):
        assert client.post("/api/auth/telegram/webapp", json={"init_data": "garbage"}).status_code == 401
    assert client.post("/api/auth/telegram/webapp", json={"init_data": sign_webapp(USER)}).status_code == 429


def test_webapp_and_bot_login_give_same_account(client, app):
    client.post("/api/auth/telegram/webapp", json={"init_data": sign_webapp(USER)})
    first = client.get("/api/account/me").json()["user"]["id"]
    other = TestClient(app, base_url="https://dekanat.test")
    sign_in_via_bot(other, app)
    assert other.get("/api/account/me").json()["user"]["id"] == first


def test_mini_app_may_be_framed_only_by_telegram(client, plain):
    h = client.get("/", follow_redirects=False).headers
    assert "x-frame-options" not in h and "web.telegram.org" in h["content-security-policy"] and "frame-ancestors" in h["content-security-policy"]
    assert client.get("/api/health").headers["x-frame-options"] == "DENY"  # остальные адреса по-прежнему закрыты


def test_no_mini_app_without_https(tmp_settings):
    tmp_settings.raw["telegram"].update(enabled=True, bot_token=TOKEN, bot_username=BOT, public_url="http://localhost:8000")
    assert TestClient(create_app(tmp_settings)).get("/api/account/me").json()["telegram"]["mini_app"] is False


# ------------------------------------------------------------------ профиль
def test_logout_keeps_account_data(client, app):
    sign_in_via_bot(client, app)
    sid = app.state.accounts.data_sid(client.cookies.get("dekanat_sid"))
    client.post("/api/assistant/message", json={"text": "Я хочу перевестись"})
    client.post("/api/assistant/message", json={"text": "да"})
    assert client.post("/api/auth/logout").json() == {"ok": True}
    assert client.get("/api/account/me").json()["user"] is None
    assert app.state.storage.sessions.load_state(sid) is not None  # анкета осталась в аккаунте
    sign_in_via_bot(client, app)
    assert client.get("/api/assistant/state").json()["dialog"]["state"] == "screening"  # и вернулась после нового входа


def test_notify_toggle_and_validation(client, app):
    assert client.patch("/api/account", json={"notify": False}).status_code == 401
    sign_in_via_bot(client, app)
    assert client.patch("/api/account", json={"notify": False}).json()["user"]["notify"] is False
    assert client.patch("/api/account", json={"notify": "maybe"}).status_code == 422


def test_foreign_origin_cannot_change_account(client, app):
    sign_in_via_bot(client, app)
    r = client.patch("/api/account", json={"notify": False}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert client.get("/api/account/me").json()["user"]["notify"] is True


def test_delete_account(client, app):
    sign_in_via_bot(client, app)
    assert client.delete("/api/account").json() == {"ok": True}
    assert client.get("/api/account/me").json()["user"] is None
    assert app.state.storage.users.count() == 0


def test_export_contains_own_data_only(client, app):
    sign_in_via_bot(client, app)
    other = TestClient(app, base_url="https://dekanat.test")
    sign_in_via_bot(other, app, {"id": 888, "username": "other", "first_name": "Пётр", "last_name": None, "language": "ru"})
    dump = client.get("/api/account/export").json()
    assert dump["profile"]["telegram_username"] == "maria_o" and "Пётр" not in json.dumps(dump, ensure_ascii=False)


# ------------------------------------------------------------------ синхронизация сайта и бота
def test_bot_progress_appears_on_site(client, app):
    """Анкета, начатая в боте (тот же AssistantCore и сессия данных аккаунта), видна на сайте после входа."""
    sign_in_via_bot(client, app)
    user = app.state.storage.users.get_by_tg(USER["id"])
    sid = app.state.accounts.ensure_data_session(user)
    app.state.core.message(sid, "Я хочу перевестись")
    app.state.core.message(sid, "да")
    state = client.get("/api/assistant/state").json()
    assert state["dialog"]["state"] == "screening" and state["dialog"]["current"]
    client.post("/api/assistant/message", json={"text": ANSWERS[0]})  # ответ на сайте — бот видит продолжение
    assert app.state.core.load_ctx(sid).data.get("student_name") == ANSWERS[0]


def test_anonymous_progress_moves_into_account_on_login(client, app):
    client.post("/api/assistant/message", json={"text": "Я хочу перевестись"})
    client.post("/api/assistant/message", json={"text": "да"})
    sign_in_via_bot(client, app)
    assert client.get("/api/assistant/state").json()["dialog"]["state"] == "screening"
    user = app.state.storage.users.get_by_tg(USER["id"])
    assert app.state.core.load_ctx(app.state.accounts.ensure_data_session(user)).state == "screening"


def test_delete_data_on_site_clears_bot_view_too(client, app):
    sign_in_via_bot(client, app)
    client.post("/api/assistant/message", json={"text": "Я хочу перевестись"})
    client.post("/api/assistant/message", json={"text": "да"})
    assert client.delete("/api/assistant/data").status_code == 200
    assert client.get("/api/account/me").json()["user"] is not None  # вход сохраняется
    user = app.state.storage.users.get_by_tg(USER["id"])
    assert app.state.core.load_ctx(app.state.accounts.ensure_data_session(user)).state == "idle"


# ------------------------------------------------------------------ обращения вошедшего
@pytest.fixture()
def desk(app, client):
    admin = TestClient(app, base_url="https://dekanat.test")
    admin_login(admin, app)
    data = build_sample_template(app.state.storage.templates_dir.parent / "t.docx").read_bytes()
    r = admin.post("/api/admin/templates", files={"file": ("perevod.docx", data, "application/octet-stream")}, data={"template_id": "perevod", "name": "Заявление"})
    assert r.status_code == 200, r.text
    return admin


def submit_ticket(c, source_client=None):
    from test_assistant import finish_questionnaire, say

    finish_questionnaire(c)
    say(c, "верно")
    assert c.post("/api/assistant/generate", json={"template_id": "perevod"}).status_code == 200
    r = c.post("/api/assistant/submission", json={"confirm": True})
    assert r.status_code == 200, r.text
    return r.json()


def test_ticket_of_signed_in_user_is_linked_and_visible_without_code(client, app, desk):
    sign_in_via_bot(client, app)
    t = submit_ticket(client)
    mine = client.get("/api/account/tickets").json()["tickets"]
    assert [x["id"] for x in mine] == [t["ticket_id"]] and "code" not in mine[0]
    stranger = TestClient(app, base_url="https://dekanat.test")
    sign_in_via_bot(stranger, app, {"id": 888, "username": "x", "first_name": "Х", "last_name": None, "language": None})
    assert stranger.get("/api/account/tickets").json()["tickets"] == []


def test_staff_reply_reaches_user_in_telegram(client, app, desk):
    sign_in_via_bot(client, app)
    t = submit_ticket(client)
    app.state.storage.db.execute("DELETE FROM notifications")  # подтверждение о приёме не интересует: проверяем ответ
    assert desk.patch(f"/api/admin/tickets/{t['ticket_id']}", json={"status": "done", "reply": "Приходите в 205 кабинет"}).status_code == 200
    queued = app.state.storage.notifications.due(10)
    assert len(queued) == 1 and queued[0]["chat_id"] == USER["id"] and "205 кабинет" in queued[0]["text"]


def test_bulk_status_change_notifies_owners_only(client, app, desk):
    sign_in_via_bot(client, app)
    t = submit_ticket(client)
    app.state.storage.db.execute("DELETE FROM notifications")
    anon, _ = app.state.storage.tickets.create("r", "other", "Другое", {}, "queue", "anon-1")
    r = desk.post("/api/admin/tickets/bulk", json={"ids": [t["ticket_id"], anon["id"]], "status": "in_progress"})
    assert r.status_code == 200 and sorted(r.json()["updated"]) == sorted([t["ticket_id"], anon["id"]])
    queued = app.state.storage.notifications.due(10)
    assert [q["chat_id"] for q in queued] == [USER["id"]]


def test_submit_confirmation_for_signed_in_user(client, app, desk):
    sign_in_via_bot(client, app)
    submit_ticket(client)
    queued = app.state.storage.notifications.due(10)
    assert any(q["chat_id"] == USER["id"] and "принято" in q["text"] for q in queued)


def test_link_old_ticket_by_code(client, app, desk):
    anon = TestClient(app, base_url="https://dekanat.test")
    t = submit_ticket(anon)
    sign_in_via_bot(client, app)
    assert client.post("/api/account/tickets/link", json={"id": t["ticket_id"], "code": "WRONG-0000"}).status_code == 404
    ok = client.post("/api/account/tickets/link", json={"id": t["ticket_id"], "code": t["code"]})
    assert ok.status_code == 200 and ok.json()["ticket"]["id"] == t["ticket_id"]
    assert [x["id"] for x in client.get("/api/account/tickets").json()["tickets"]] == [t["ticket_id"]]


def test_link_attempts_are_limited(client, app):
    sign_in_via_bot(client, app)
    codes = [client.post("/api/account/tickets/link", json={"id": 1, "code": f"BAD-{i:04d}"}).status_code for i in range(12)]
    assert codes[0] == 404 and codes[-1] == 429


# ------------------------------------------------------------------ webhook
def test_webhook_is_closed_in_polling_mode(client):
    assert client.post("/api/telegram/webhook/anything", json={}).status_code == 404


def test_webhook_checks_path_and_header(tmp_settings):
    app = create_app(tg_settings(tmp_settings, mode="webhook"))
    c = TestClient(app)
    tg = app.state.telegram
    got = []
    tg.dispatch = got.append
    secret_header = hashlib.sha256(f"hook-secret|{tg.webhook_secret or ''}|{TOKEN}".encode()).hexdigest()[:48]
    assert c.post("/api/telegram/webhook/wrongpath", json={"update_id": 1}, headers={"x-telegram-bot-api-secret-token": secret_header}).status_code == 404
    assert c.post(tg.webhook_path, json={"update_id": 1}).status_code == 403
    assert c.post(tg.webhook_path, json={"update_id": 1}, headers={"x-telegram-bot-api-secret-token": "nope"}).status_code == 403
    assert not got
    r = c.post(tg.webhook_path, json={"update_id": 2, "message": {}}, headers={"x-telegram-bot-api-secret-token": secret_header})
    assert r.status_code == 200 and got == [{"update_id": 2, "message": {}}]
    assert TOKEN not in tg.webhook_path  # токен в адресе не светится


def test_admin_sees_bot_state_and_account_counts(client, app, desk):
    info = desk.get("/api/admin/info").json()["telegram"]
    assert info["enabled"] is True and info["users"] == 0 and info["web_linked"] == 0 and TOKEN not in json.dumps(info)
    sign_in_via_bot(client, app)
    info = desk.get("/api/admin/info").json()["telegram"]
    assert info["users"] == 1 and info["web_linked"] == 1


def test_public_health_hides_bot_internals(client):
    tg = client.get("/api/health").json()["telegram"]
    assert tg == {"enabled": True, "login": True, "bot": BOT, "mini_app": True}  # без ошибок, режима и токена
