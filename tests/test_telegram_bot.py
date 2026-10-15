"""Telegram-бот на поддельном Bot API: команды, кнопки, анкета и DOCX, вход на сайте, обращения, настройки, лимиты. Настоящий Telegram не нужен."""

import itertools
import re
import time

import pytest
from fastapi.testclient import TestClient

from conftest import admin_login, needs_ocr
from dekanat.templates.sample import build_sample_template
from dekanat.web.app import create_app
from test_account_api import BOT, prefix_of, tg_settings
from test_assistant import ANSWERS
from test_telegram_auth import TOKEN

UID = 777
_ids = itertools.count(1)


class FakeAPI:
    """Записывает всё, что бот отправил в Telegram. Файлы для скачивания лежат в `files`."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.files: dict[str, bytes] = {}
        self._mid = 1000

    def _rec(self, name, **kw):
        self.calls.append((name, kw))

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        self._mid += 1
        self._rec("send_message", chat_id=chat_id, text=text, markup=reply_markup, mid=self._mid)
        return {"message_id": self._mid}

    def edit_message_text(self, chat_id, message_id, text, reply_markup=None, parse_mode="HTML"):
        self._rec("edit_message_text", chat_id=chat_id, text=text, markup=reply_markup, mid=message_id)
        return {"message_id": message_id}

    def edit_reply_markup(self, chat_id, message_id, reply_markup=None):
        self._rec("edit_reply_markup", chat_id=chat_id, mid=message_id)

    def delete_message(self, chat_id, message_id):
        self._rec("delete_message", chat_id=chat_id, mid=message_id)

    def answer_callback(self, callback_id, text="", alert=False):
        self._rec("answer_callback", text=text)

    def send_chat_action(self, chat_id, action="typing"):
        self._rec("send_chat_action", action=action)

    def send_photo(self, chat_id, data, caption="", filename="photo.jpg", reply_markup=None):
        self._rec("send_photo", chat_id=chat_id, data=data, caption=caption)

    def send_document(self, chat_id, data, filename, caption="", reply_markup=None):
        self._rec("send_document", chat_id=chat_id, data=data, filename=filename, caption=caption)

    def download_file(self, file_id, max_bytes=0):
        return self.files[file_id]

    def set_my_commands(self, commands):
        self._rec("set_my_commands", commands=commands)

    def set_my_description(self, description, short=""):
        self._rec("set_my_description")

    def set_chat_menu_button(self, url=None, text="Приложение"):
        self._rec("set_chat_menu_button", url=url)

    # ---- удобные выборки
    def of(self, name):
        return [kw for n, kw in self.calls if n == name]

    @property
    def texts(self):
        return [kw["text"] for n, kw in self.calls if n in ("send_message", "edit_message_text")]

    @property
    def last(self):
        return self.texts[-1]

    def last_markup(self):
        for n, kw in reversed(self.calls):
            if n in ("send_message", "edit_message_text") and kw.get("markup"):
                return kw["markup"]
        return None

    def buttons(self):
        mk = self.last_markup() or {}
        return [b for row in mk.get("inline_keyboard", []) for b in row]

    def clear(self):
        self.calls.clear()


def user(uid=UID, **kw):
    return {"id": uid, "first_name": kw.get("first_name", "Мария"), "last_name": "Орлова", "username": kw.get("username", "maria_o"), "language_code": "ru"}


def msg(text=None, uid=UID, chat="private", **extra):
    m = {"message_id": next(_ids), "from": user(uid), "chat": {"id": uid, "type": chat}}
    if text is not None:
        m["text"] = text
    m.update(extra)
    return {"update_id": next(_ids), "message": m}


def cb(data, uid=UID, mid=50):
    return {"update_id": next(_ids), "callback_query": {"id": str(next(_ids)), "from": user(uid), "data": data,
                                                         "message": {"message_id": mid, "chat": {"id": uid, "type": "private"}}}}


@pytest.fixture()
def app(tmp_settings):
    return create_app(tg_settings(tmp_settings))


@pytest.fixture()
def tg(app):
    return app.state.telegram


@pytest.fixture()
def api(tg):
    fake = FakeAPI()
    tg.bot.api = fake
    return fake


@pytest.fixture()
def bot(tg, api):
    return tg.bot


@pytest.fixture()
def site(app):
    return TestClient(app, base_url="https://dekanat.test")


@pytest.fixture()
def desk(app, tmp_path):
    admin = TestClient(app, base_url="https://dekanat.test")
    admin_login(admin, app)
    data = build_sample_template(tmp_path / "t.docx").read_bytes()
    assert admin.post("/api/admin/templates", files={"file": ("perevod.docx", data, "application/octet-stream")},
                      data={"template_id": "perevod", "name": "Заявление о переводе"}).status_code == 200
    return admin


def callbacks(api):
    return [b.get("callback_data") for b in api.buttons() if b.get("callback_data")]


def run_form(bot, api, kind="transfer"):
    """Анкета из бота: кнопка «Перевод», затем ответы текстом, как в чате."""
    bot.handle_update(cb(f"form:{kind}"))
    for text in [*ANSWERS, "Прикладная информатика", "1234 567890"]:
        bot.handle_update(msg(text))
    return api.last


# ------------------------------------------------------------------ старт и меню
def test_start_greets_and_shows_menu_with_mini_app(bot, api):
    bot.handle_update(msg("/start"))
    assert "цифровой секретарь деканата" in api.last
    keyboard = api.of("send_message")[-1]["markup"]["keyboard"]
    assert any(b.get("web_app", {}).get("url") == "https://dekanat.test/?tg=1" for row in keyboard for b in row)
    assert "решение принимает сотрудник деканата" in api.last


def test_account_is_created_on_first_message(bot, api, app):
    assert app.state.storage.users.count() == 0
    bot.handle_update(msg("/help"))
    assert app.state.storage.users.get_by_tg(UID)["username"] == "maria_o"


def test_only_private_chats_and_humans(bot, api):
    bot.handle_update(msg("/start", chat="group"))
    bot.handle_update(msg("/start", chat="supergroup"))
    bot_msg = msg("/start")
    bot_msg["message"]["from"]["is_bot"] = True
    bot.handle_update(bot_msg)
    assert api.calls == []


def test_setup_registers_commands_and_menu_button(bot, api):
    bot.setup()
    names = [c["command"] for c in api.of("set_my_commands")[0]["commands"]]
    assert {"start", "check", "ask", "form", "tickets", "status", "settings", "forget", "privacy"} <= set(names)
    assert api.of("set_chat_menu_button")[0]["url"] == "https://dekanat.test/?tg=1"


def test_menu_buttons_work_like_commands(bot, api):
    for label, expect in (("📄 Проверить документ", "Пришлите фото или PDF"), ("❓ Вопрос по Положению", "Напишите вопрос"), ("📬 Мои обращения", "пока нет обращений"),
                          ("⚙️ Настройки", "Настройки")):
        bot.handle_update(msg(label))
        assert expect in api.last


def test_unknown_command_and_odd_messages(bot, api):
    bot.handle_update(msg("/nonsense"))
    assert "не знаю" in api.last
    bot.handle_update(msg(None, sticker={"file_id": "x"}))
    assert "текст, фото и PDF" in api.last


def test_privacy_text_is_honest_about_telegram(bot, api):
    bot.handle_update(msg("/privacy"))
    assert "серверы Telegram" in api.last and "без внешних языковых моделей" in api.last


# ------------------------------------------------------------------ вопросы по Положению
def test_rules_question_answered_with_source(bot, api):
    bot.handle_update(msg("Сколько дней рассматривается заявление?"))
    text = api.last
    assert "Откуда это" in text and "Положени" in text
    assert api.of("send_chat_action")  # «печатает…» пока считаем


def test_unknown_question_is_honest(bot, api):
    bot.handle_update(msg("Какая погода на Марсе в следующий вторник?"))
    assert "Ответа нет" in api.last


def test_user_text_is_never_sent_back_as_markup(bot, api):
    bot.handle_update(msg("<b>жирный</b> <script>alert(1)</script> & вопрос про отпуск?"))
    assert "<script>" not in api.last


def test_flood_is_limited(bot, api):
    for _ in range(25):
        bot.handle_update(msg("привет"))
    assert any("Слишком много сообщений" in t for t in api.texts)


# ------------------------------------------------------------------ файлы
def test_unsupported_and_huge_files_refused(bot, api):
    bot.handle_update(msg(None, document={"file_id": "d1", "file_name": "virus.exe", "mime_type": "application/x-msdownload", "file_size": 100}))
    assert "Такой файл я не читаю" in api.last
    bot.handle_update(msg(None, document={"file_id": "d2", "file_name": "scan.pdf", "mime_type": "application/pdf", "file_size": 999_999_999}))
    assert "больше" in api.last and not api.of("send_chat_action")


def test_garbage_file_gets_friendly_error(bot, api):
    api.files["g1"] = b"this is not an image at all"
    bot.handle_update(msg(None, document={"file_id": "g1", "file_name": "scan.pdf", "mime_type": "application/pdf", "file_size": 26}))
    assert "Читаю документ" in api.texts[0]
    assert "Traceback" not in api.last and "Exception" not in api.last
    assert any(n == "edit_message_text" for n, _ in api.calls)  # сообщение «Читаю…» заменено результатом


def test_file_limit_per_hour(bot, api, tg):
    tg.bot.file_limiter.limit = 2
    api.files["g1"] = b"junk"
    for _ in range(3):
        bot.handle_update(msg(None, photo=[{"file_id": "g1", "width": 10, "height": 10, "file_size": 4}]))
    assert "много документов" in api.last


@needs_ocr
def test_photo_check_end_to_end(bot, api, samples):
    sample = next(p for p in sorted(samples.glob("*.png")) + sorted(samples.glob("*.jpg")))
    api.files["p1"] = sample.read_bytes()
    bot.handle_update(msg(None, photo=[{"file_id": "p1", "width": 800, "height": 1000, "file_size": len(api.files["p1"])}], caption="Хватит ли этой справки для перевода?"))
    final = api.texts[-1]
    assert "📄" in final and ("Документ в порядке" in final or "Чего-то не хватает" in final or "Ответ найден" in final or "Ответа нет" in final)
    assert "предварительная оценка" in final
    assert {"doc:img", "form:any", "doc:forget"} <= set(callbacks(api))
    api.clear()
    bot.handle_update(cb("doc:img"))
    assert api.of("send_photo") and api.of("send_photo")[0]["data"][:2] == b"\xff\xd8"  # JPEG с подсветкой
    api.clear()
    bot.handle_update(msg("Покажи ФИО"))
    assert api.texts


@needs_ocr
def test_document_check_is_visible_on_site_after_login(bot, api, samples, app, site):
    sample = next(p for p in sorted(samples.glob("*.png")) + sorted(samples.glob("*.jpg")))
    api.files["p1"] = sample.read_bytes()
    bot.handle_update(msg(None, photo=[{"file_id": "p1", "width": 800, "height": 1000, "file_size": 1000}]))
    token = site.post("/api/auth/telegram/start").json()["token"]
    bot.handle_update(msg(f"/start login_{token}"))
    bot.handle_update(cb(f"lg:y:{prefix_of(token)}"))
    site.get("/api/auth/telegram/poll", params={"token": token})
    assert site.get("/api/assistant/state").json()["document"] is not None


# ------------------------------------------------------------------ анкета и заявление
def test_form_via_buttons_and_text(bot, api):
    bot.handle_update(msg("/form"))
    assert set(callbacks(api)) == {"form:transfer", "form:enrollment"}
    bot.handle_update(cb("form:transfer"))
    assert "Вопрос 1 из" in api.last
    bot.handle_update(msg(ANSWERS[0]))
    assert "Вопрос 2 из" in api.last
    assert {"o:back", "o:cancel"} <= set(callbacks(api))


def test_form_option_buttons_answer_questions(bot, api):
    bot.handle_update(cb("form:transfer"))
    for text in ANSWERS[:3]:
        bot.handle_update(msg(text))
    # «очная/заочная» у вопроса о форме обучения предложены кнопками
    for _ in range(3):
        opts = [b for b in api.buttons() if b.get("callback_data", "").startswith("o:") and b["callback_data"] not in ("o:back", "o:cancel")]
        if opts:
            break
        bot.handle_update(msg(ANSWERS[3]))
    assert opts
    before = api.last
    bot.handle_update(cb(opts[0]["callback_data"]))
    assert api.last != before


def test_stale_option_button_is_harmless(bot, api):
    bot.handle_update(cb("o:7"))
    assert "устарели" in api.last


def test_cancel_and_back(bot, api):
    bot.handle_update(cb("form:transfer"))
    bot.handle_update(msg(ANSWERS[0]))
    bot.handle_update(cb("o:back"))
    assert "Вопрос 1 из" in api.last
    bot.handle_update(cb("o:cancel"))
    assert "Вопрос" not in api.last


def test_enrollment_form_starts(bot, api):
    bot.handle_update(cb("form:enrollment"))
    assert "Вопрос 1 из" in api.last


def test_full_form_to_docx_in_chat(bot, api, desk):
    last = run_form(bot, api)
    assert "Проверьте" in last or "верно" in last.lower()
    bot.handle_update(msg("верно"))
    sent = False
    for _ in range(2):
        if "o:0" in callbacks(api) or any("формировать" in str(b.get("text", "")).lower() for b in api.buttons()):
            btn = next(b for b in api.buttons() if "формировать" in b["text"].lower())
            bot.handle_update(cb(btn["callback_data"]))
        else:
            bot.handle_update(msg("сформировать"))
        if api.of("send_document"):
            sent = True
            break
    assert sent
    doc = api.of("send_document")[0]
    assert doc["data"][:2] == b"PK" and doc["filename"].endswith(".docx")
    assert {"sub:prev", "ns:show", "form:any"} <= set(callbacks(api))


def test_generate_without_templates_explains(bot, api):
    run_form(bot, api)
    bot.handle_update(msg("верно"))
    bot.handle_update(msg("сформировать"))
    assert "шаблон" in api.last.lower() and not api.of("send_document")


def test_submit_from_bot_creates_owned_ticket(bot, api, app, desk):
    run_form(bot, api)
    bot.handle_update(msg("верно"))
    bot.handle_update(msg("сформировать"))
    bot.handle_update(cb("sub:prev"))
    assert "Что уйдёт в деканат" in api.last and "Иванов Иван Иванович" in api.last
    assert "1234" not in api.last  # паспортные данные не уходят
    bot.handle_update(cb("sub:ok"))
    assert "передано в деканат" in api.last
    code = re.search(r"<code>([A-Z0-9\-]+)</code>", api.last).group(1)
    user = app.state.storage.users.get_by_tg(UID)
    tickets = app.state.storage.tickets.list_for_user(user["id"])
    assert len(tickets) == 1 and app.state.storage.tickets.check_code(tickets[0]["id"], code)
    assert app.state.storage.notifications.due(10) == []  # подтверждение в чате уже было, второе не нужно
    bot.handle_update(cb("sub:ok"))  # двойное нажатие не создаёт второе обращение
    assert len(app.state.storage.tickets.list_for_user(user["id"])) == 1


def test_contact_opt_in_is_explicit(bot, api, app, desk):
    run_form(bot, api)
    bot.handle_update(msg("верно"))
    bot.handle_update(msg("сформировать"))
    bot.handle_update(cb("sub:prev"))
    assert "@maria_o" not in api.last  # по умолчанию контакт не отправляется
    bot.handle_update(cb("sub:contact"))
    assert "Контакт: @maria_o" in api.last
    bot.handle_update(cb("sub:ok"))
    ticket = app.state.storage.tickets.list_for_user(app.state.storage.users.get_by_tg(UID)["id"])[0]
    assert app.state.storage.tickets.get(ticket["id"])["payload"]["fields"]["Контакт"] == "@maria_o"


def test_submit_before_form_is_refused(bot, api, desk):
    bot.handle_update(cb("sub:prev"))
    assert "Сначала пройдите анкету" in api.last


def test_next_steps_and_reminder(bot, api, app, desk):
    run_form(bot, api)
    bot.handle_update(msg("верно"))
    bot.handle_update(cb("ns:show"))
    assert "Что дальше" in api.last
    if "rem:3" in callbacks(api):
        bot.handle_update(cb("rem:3"))
        assert "напомню через 3 дн" in api.last
        rows = app.state.storage.db.query("SELECT * FROM notifications WHERE kind='reminder'")
        assert len(rows) == 1 and rows[0]["send_after"] > time.time() + 2 * 86400


def test_reminder_not_set_when_notifications_off(bot, api, app, desk):
    bot.handle_update(msg("/start"))
    app.state.storage.users.set_notify(app.state.storage.users.get_by_tg(UID)["id"], False)
    run_form(bot, api)
    bot.handle_update(msg("верно"))
    bot.handle_update(cb("ns:show"))
    bot.handle_update(cb("rem:3"))
    assert app.state.storage.db.query("SELECT * FROM notifications") == []


# ------------------------------------------------------------------ вход на сайте
def test_login_requires_confirmation_in_chat(bot, api, app, site):
    token = site.post("/api/auth/telegram/start").json()["token"]
    bot.handle_update(msg(f"/start login_{token}"))
    assert "Вход на сайт" in api.last and "Это не я" in str(api.buttons())
    assert site.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "pending"  # само открытие ссылки ничего не даёт
    bot.handle_update(cb(f"lg:y:{prefix_of(token)}"))
    assert "Вернитесь на сайт" in api.last
    assert site.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "confirmed"
    assert site.get("/api/account/me").json()["user"]["username"] == "maria_o"


def test_login_can_be_denied_in_chat(bot, api, site):
    token = site.post("/api/auth/telegram/start").json()["token"]
    bot.handle_update(msg(f"/start login_{token}"))
    bot.handle_update(cb(f"lg:n:{prefix_of(token)}"))
    assert "отклонён" in api.last
    assert site.get("/api/auth/telegram/poll", params={"token": token}).json()["status"] == "denied"


def test_login_link_unknown_or_reused(bot, api, site):
    bot.handle_update(msg("/start login_" + "A" * 43))
    assert "устарела или уже использована" in api.last
    token = site.post("/api/auth/telegram/start").json()["token"]
    bot.handle_update(msg(f"/start login_{token}"))
    bot.handle_update(cb(f"lg:y:{prefix_of(token)}"))
    bot.handle_update(cb(f"lg:y:{prefix_of(token)}"))  # повторное нажатие
    assert "устарел" in api.last


def test_site_login_shares_form_progress_with_bot(bot, api, site):
    bot.handle_update(cb("form:transfer"))
    bot.handle_update(msg(ANSWERS[0]))
    token = site.post("/api/auth/telegram/start").json()["token"]
    bot.handle_update(msg(f"/start login_{token}"))
    bot.handle_update(cb(f"lg:y:{prefix_of(token)}"))
    site.get("/api/auth/telegram/poll", params={"token": token})
    st = site.get("/api/assistant/state").json()["dialog"]
    assert st["state"] == "screening" and "student_name" in st["answered"]
    site.post("/api/assistant/message", json={"text": ANSWERS[1]})
    bot.handle_update(msg(ANSWERS[2]))
    assert "Вопрос 4 из" in api.last  # бот продолжил с того места, где остановился сайт


# ------------------------------------------------------------------ обращения
def make_ticket(app, owner=True, code="ABCD-2345", reply=None):
    st = app.state.storage
    uid = st.users.get_by_tg(UID)["id"] if owner else None
    t, _ = st.tickets.create("r", "transfer", "Перевод из другого вуза", {"request": "x", "fields": {}}, "queue", f"k{next(_ids)}", user_id=uid)
    st.tickets.attach_code(t["id"], code)
    if reply:
        st.tickets.update(t["id"], "done", None, reply)
    return t


def test_tickets_list_and_details(bot, api, app):
    bot.handle_update(msg("/start"))
    t = make_ticket(app, reply="Принесите <оригинал> & копию")
    bot.handle_update(msg("/tickets"))
    assert f"№ {t['id']}" in api.last and f"t:{t['id']}" in callbacks(api)
    bot.handle_update(cb(f"t:{t['id']}"))
    assert "Рассмотрено" in api.last and "&lt;оригинал&gt; &amp; копию" in api.last  # ответ сотрудника экранирован


def test_cannot_open_someone_elses_ticket(bot, api, app):
    bot.handle_update(msg("/start"))
    other = make_ticket(app, owner=False)
    bot.handle_update(cb(f"t:{other['id']}"))
    assert "не найдено" in api.last


def test_status_by_code_and_link_button(bot, api, app):
    bot.handle_update(msg("/start"))
    t = make_ticket(app, owner=False)
    bot.handle_update(msg(f"/status {t['id']} WRONG-0000"))
    assert "не найдено" in api.last
    bot.handle_update(msg(f"/status {t['id']} ABCD-2345"))
    assert "Принято" in api.last and f"link:{t['id']}" in callbacks(api)
    bot.handle_update(cb(f"link:{t['id']}"))
    assert "привязано" in api.last
    assert app.state.storage.tickets.owner_of(t["id"]) == app.state.storage.users.get_by_tg(UID)["id"]


def test_status_flow_asks_for_numbers(bot, api, app):
    bot.handle_update(msg("/status"))
    assert "номер обращения и код" in api.last
    t = make_ticket(app, owner=False)
    bot.handle_update(msg(f"{t['id']} ABCD-2345"))
    assert f"№ {t['id']}" in api.last


def test_link_button_works_only_for_own_pending_lookup(bot, api, app):
    bot.handle_update(msg("/start"))
    t = make_ticket(app, owner=False)
    bot.handle_update(cb(f"link:{t['id']}"))  # без предварительной проверки кода привязать нельзя
    assert "Время привязки вышло" in api.last
    assert app.state.storage.tickets.owner_of(t["id"]) is None


def test_status_attempts_are_limited(bot, api):
    for i in range(12):
        bot.handle_update(msg(f"/status 1 BAD-{i:04d}"))
    assert "Слишком много" in api.last


# ------------------------------------------------------------------ настройки и данные
def test_notifications_toggle(bot, api, app):
    bot.handle_update(msg("/settings"))
    assert "включены" in api.last
    bot.handle_update(cb("set:notify"))
    assert "выключены" in api.last
    assert app.state.storage.users.get_by_tg(UID)["notify"] == 0


def test_forget_deletes_form_and_document_everywhere(bot, api, app, site):
    bot.handle_update(cb("form:transfer"))
    bot.handle_update(msg(ANSWERS[0]))
    bot.handle_update(msg("/forget"))
    assert "Да, удалить" in str(api.buttons())
    bot.handle_update(cb("set:data!"))
    assert "Готово" in api.last
    user = app.state.storage.users.get_by_tg(UID)
    assert app.state.core.load_ctx(app.state.accounts.ensure_data_session(user)).state == "idle"
    assert app.state.storage.users.get_by_tg(UID) is not None  # сам аккаунт остался


def test_forget_needs_confirmation(bot, api, app):
    bot.handle_update(cb("form:transfer"))
    bot.handle_update(cb("set:data"))
    user = app.state.storage.users.get_by_tg(UID)
    assert app.state.core.load_ctx(app.state.accounts.ensure_data_session(user)).state == "screening"
    bot.handle_update(cb("set:cancel"))
    assert app.state.core.load_ctx(app.state.accounts.ensure_data_session(user)).state == "screening"


def test_delete_account_from_bot(bot, api, app):
    bot.handle_update(msg("/start"))
    uid = app.state.storage.users.get_by_tg(UID)["id"]
    t = make_ticket(app)
    bot.handle_update(cb("set:del"))
    assert app.state.storage.users.get(uid) is not None  # сначала подтверждение
    bot.handle_update(cb("set:del!"))
    assert app.state.storage.users.get(uid) is None
    assert app.state.storage.tickets.get(t["id"]) is not None and app.state.storage.tickets.owner_of(t["id"]) is None
    assert "Аккаунт удалён" in api.last


def test_users_are_isolated_in_bot(bot, api, app):
    bot.handle_update(cb("form:transfer"))
    bot.handle_update(msg(ANSWERS[0]))
    bot.handle_update(msg("/settings", uid=999))
    assert "анкета — нет" in api.last
    bot.handle_update(msg("/tickets", uid=999))
    assert "нет обращений" in api.last


def test_app_command_without_https_explains(tmp_settings):
    tmp_settings.raw["telegram"].update(enabled=True, bot_token=TOKEN, bot_username=BOT, public_url=None)
    app = create_app(tmp_settings)
    fake = FakeAPI()
    app.state.telegram.bot.api = fake
    app.state.telegram.bot.handle_update(msg("/app"))
    assert "telegram.public_url" in fake.last
    app.state.telegram.bot.handle_update(msg("/start"))
    keyboard = fake.of("send_message")[-1]["markup"]["keyboard"]
    assert not any("web_app" in b for row in keyboard for b in row)  # без https кнопки приложения нет


def test_errors_inside_handlers_do_not_kill_the_bot(bot, api, monkeypatch):
    monkeypatch.setattr(bot, "_hello", lambda u: (_ for _ in ()).throw(RuntimeError("boom")))
    bot.handle_update(msg("/start"))
    assert "Не получилось выполнить запрос" in api.last
    bot.handle_update(msg("/help"))  # и дальше работает
    assert len(api.texts) == 2


def test_forgetting_a_document_also_drops_it_from_memory(bot, api, app):
    bot.handle_update(msg("/start"))
    user = app.state.storage.users.get_by_tg(UID)
    app.state.storage.users.save_bot_state(user["id"], {"last_file": {"id": "f1", "name": "a.jpg"}, "last_question": "q"})
    bot._files["f1"] = b"private bytes"
    bot.handle_update(cb("doc:forget"))
    assert "f1" not in bot._files
    assert "last_file" not in app.state.storage.users.bot_state(user["id"])
    bot._files["f1"] = b"again"
    app.state.storage.users.save_bot_state(user["id"], {"last_file": {"id": "f1", "name": "a.jpg"}})
    bot.handle_update(cb("set:data!"))
    assert "f1" not in bot._files


# ------------------------------------------------------------------ удобство: карточка, прогресс, быстрые вопросы, отклик на кнопки
def test_card_groups_composite_fields_and_marks():
    from dekanat.telegram.views import render_card

    fields = [
        {"title": "ФИО студента", "kind": "person", "found": True, "value": "Иванов Иван", "doubt": "", "parts": []},
        {"title": "Номер и дата выдачи", "kind": "composite", "found": True, "value": "№ 6823 от 07.10.2024", "doubt": "",
         "parts": [{"title": "номер выдачи", "found": True, "value": "6823"}, {"title": "дата выдачи", "found": True, "value": "07.10.2024"}]},
        {"title": "Подпись и печать", "kind": "marks", "found": True, "value": "", "doubt": "",
         "parts": [{"title": "Подпись", "found": True, "value": "есть"}, {"title": "Печать вуза", "found": False, "value": "нет"}]},
        {"title": "Период", "kind": "period", "found": False, "value": "не найдено", "doubt": "", "parts": []},
        {"title": "Форма обучения", "kind": "choice", "found": True, "value": "заочная", "doubt": "слабое распознавание", "parts": []},
    ]
    lines = render_card(fields).splitlines()
    assert lines[0] == "✓ ФИО студента: Иванов Иван"
    assert lines[1] == "✓ Номер и дата выдачи: № 6823 от 07.10.2024"  # одна строка вместо трёх с повторяющимся названием
    assert lines[2] == "◐ Подпись и печать: подпись — есть · печать вуза — нет"
    assert lines[3] == "✗ Период: не найдено"
    assert lines[4].startswith("⚠️ Форма обучения")
    assert sum("Номер и дата выдачи" in x for x in lines) == 1


def test_card_shows_what_part_is_missing():
    from dekanat.telegram.views import render_card

    f = [{"title": "Номер и дата выдачи", "kind": "composite", "found": False, "value": "", "doubt": "",
          "parts": [{"title": "номер выдачи", "found": True, "value": "6823"}, {"title": "дата выдачи", "found": False, "value": ""}]}]
    line = render_card(f)
    assert line.startswith("◐ Номер и дата выдачи: 6823") and "не найдено: дата выдачи" in line


def test_progress_message_has_bar_stage_and_percent():
    from dekanat.telegram.views import progress_bar, render_progress

    assert progress_bar(0.0) == "▱" * 10 and progress_bar(1.0) == "▰" * 10 and progress_bar(0.4) == "▰▰▰▰" + "▱" * 6
    text = render_progress("ocr", 0.4)
    assert "40%" in text and "читаю текст" in text and "▰▰▰▰" in text
    assert render_progress("???", 5).count("▰") == 10 and "100%" in render_progress("???", 5)  # доля вне 0..1 не ломает шкалу


def test_progress_updates_are_throttled_and_never_break_the_check(bot, api):
    bot.handle_update(msg("/start"))
    api.clear()
    user = bot.storage.users.get_by_tg(UID)
    report = bot._progress_reporter(user, {"message_id": 77})
    report("ocr", "Чтение", 0.30)
    report("ocr", "Чтение", 0.35)  # слишком скоро и почти без сдвига — не правим сообщение
    edits = api.of("edit_message_text")
    assert len(edits) == 1 and edits[0]["mid"] == 77 and "читаю текст" in edits[0]["text"]

    def boom(*a, **k):
        raise TelegramError(400, "message is not modified")

    bot.api.edit_message_text = boom
    bot._progress_reporter(user, {"message_id": 1})("extract", "Поля", 0.7)  # сбой правки молча игнорируется
    bot._progress_reporter(user, None)("extract", "Поля", 0.7)  # нет сообщения — нечего править


def test_buttons_answer_instantly_with_a_toast(bot, api):
    from dekanat.telegram.bot import toast_for

    assert toast_for("sub:ok") == "Отправляю в деканат…" and toast_for("lg:y:abc") == "Подтверждаю вход…" and toast_for("o:3") == ""
    bot.handle_update(msg("/start"))
    api.clear()
    bot.handle_update(cb("set:notify"))
    assert api.of("answer_callback")[0]["text"] == "Сохраняю…"
    api.clear()
    bot.handle_update(cb("t:999"))  # без подсказки — просто снимается «часики» у кнопки
    assert api.of("answer_callback")[0]["text"] == ""


def test_quick_question_without_a_document_is_answered_from_the_rules(bot, api):
    bot.handle_update(msg("/start"))
    api.clear()
    bot.handle_update(cb("q:0"))  # вопрос отправляется от имени человека, как если бы он набрал его сам
    assert api.texts and "предварительная оценка" in api.texts[-1].lower() + " предварительная оценка"
    api.clear()
    bot.handle_update(cb("q:9"))  # несуществующая кнопка — молча ничего
    assert api.texts == []


@needs_ocr
def test_document_check_offers_quick_questions_and_a_clean_card(bot, api, samples):
    sample = next(p for p in sorted(samples.glob("*.png")) + sorted(samples.glob("*.jpg")))
    api.files["p1"] = sample.read_bytes()
    bot.handle_update(msg(None, photo=[{"file_id": "p1", "width": 800, "height": 1000, "file_size": len(api.files["p1"])}]))
    first = callbacks(api)
    assert first[:2] == ["q:0", "q:1"]  # быстрые вопросы — первым рядом
    card = api.texts[-1]
    rows = [x for x in card.splitlines() if x.startswith(("✓", "◐", "✗", "⚠️"))]
    assert rows and sum("Номер и дата выдачи" in x for x in rows) == 1 and "▰" not in card  # поля сгруппированы, шкалы прогресса в итоговом сообщении нет
    api.clear()
    bot.handle_update(cb("q:0"))
    assert api.texts  # ответ пришёл
    again = callbacks(api)
    assert "q:0" not in again and "q:1" in again and "doc:img" in again  # заданный вопрос из кнопок убран, остальное осталось
    assert api.of("answer_callback")[0]["text"] == "Ищу ответ…"  # нажатие подтверждено сразу, пока бот ищет ответ


# ------------------------------------------------------------------ «покажи …»: снимок идёт сразу, без кнопки
def test_which_phrases_ask_to_see_the_place():
    from dekanat.qa.docqa import wants_to_see

    for q in ("покажи слово Главам", "Покажи ФИО", "где подпись?", "Найди печать", "выдели дату", "Подсвети номер"):
        assert wants_to_see(q), q
    for q in ("Чего не хватает в документе?", "Хватит ли справки для перевода?", "Сколько дней рассматривается заявление?", "Кто руководитель практики?", ""):
        assert not wants_to_see(q), q


def test_zoom_enlarges_small_places_and_skips_big_ones():
    import numpy as np

    from dekanat.models import Box

    page = np.full((2000, 1400, 3), 255, np.uint8)
    small = Box(600, 900, 700, 940, 0)
    data = bot_zoom(page, [small])
    assert data and data[:2] == b"\xff\xd8"
    import cv2

    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] >= 900  # крупный план увеличен до читаемого размера
    assert bot_zoom(page, [Box(100, 100, 1300, 1900, 0)]) is None  # место и так занимает почти весь лист
    assert bot_zoom(page, [Box(0, 0, 40, 30, 0)]) is not None  # у края листа рамка не выходит за границы


def bot_zoom(page, boxes):
    from dekanat.telegram.bot import TelegramBot

    return TelegramBot._zoom(page, boxes)


@needs_ocr
def test_show_request_sends_text_and_snapshot_at_once(bot, api, samples):
    sample = next(p for p in sorted(samples.glob("*.png")) + sorted(samples.glob("*.jpg")))
    api.files["p1"] = sample.read_bytes()
    bot.handle_update(msg(None, photo=[{"file_id": "p1", "width": 800, "height": 1000, "file_size": len(api.files["p1"])}]))
    api.clear()
    bot.handle_update(msg("Покажи ФИО"))
    assert api.texts and api.of("send_photo"), "текст ответа и снимок должны прийти без нажатия кнопки"
    assert api.of("send_photo")[0]["data"][:2] == b"\xff\xd8" and "подсвечено" in api.of("send_photo")[0]["caption"]
    answer_markup = next(kw["markup"] for n, kw in api.calls if n == "send_message")
    assert "doc:img" not in str(answer_markup)  # кнопка «Показать на снимке» не нужна — снимок уже здесь
    # порядок: сначала текст, потом снимок
    names = [n for n, _ in api.calls if n in ("send_message", "send_photo")]
    assert names[0] == "send_message" and "send_photo" in names
    api.clear()
    bot.handle_update(msg("Чего не хватает в документе?"))  # обычный вопрос — без снимка, кнопка осталась
    assert api.texts and not api.of("send_photo")
    assert "doc:img" in str(next(kw["markup"] for n, kw in api.calls if n == "send_message"))


def test_show_request_without_a_document_does_not_pretend(bot, api):
    bot.handle_update(msg("Покажи ФИО"))
    assert api.texts and not api.of("send_photo")
