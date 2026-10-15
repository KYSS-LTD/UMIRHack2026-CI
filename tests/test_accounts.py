"""Аккаунты через Telegram: слой хранения, вход через бота, перенос анонимных данных, очередь уведомлений. Без сети и без настоящего бота."""

import hashlib
import time

import pytest

from dekanat.accounts import AccountError, Accounts, describe_device, display_name, public_user
from dekanat.storage import Storage
from dekanat.telegram.notify import Notifier

PROFILE = {"id": 555, "username": "student", "first_name": "Мария", "last_name": "Орлова", "language": "ru"}


@pytest.fixture()
def storage(tmp_settings):
    st = Storage(tmp_settings)
    yield st
    st.close()


@pytest.fixture()
def accounts(storage, tmp_settings):
    return Accounts(storage, tmp_settings)


def prefix_of(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()[:24]


def login(accounts, storage, profile=PROFILE, cookie=None):
    """Полный вход: запрос на сайте -> подтверждение в боте -> опрос сайтом. Возвращает (аккаунт, новая cookie, что перенесено)."""
    cookie = cookie or storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "Chrome, Windows")
    assert accounts.answer_login(prefix_of(token), profile, True) == "ok"
    status, prof = accounts.poll_login(token, cookie)
    assert status == "confirmed"
    return accounts.login_session(prof, cookie)


# ------------------------------------------------------------------ пользователи
def test_upsert_creates_then_updates_profile(storage):
    user, created = storage.users.upsert_tg(7, "old", "Имя", None, "ru")
    assert created and user["tg_id"] == 7 and user["notify"] == 1
    again, created2 = storage.users.upsert_tg(7, "new", "Имя2", None, "ru")
    assert not created2 and again["id"] == user["id"] and again["username"] == "new" and again["first_name"] == "Имя2"
    assert storage.users.count() == 1


def test_display_name_and_public_user_hide_internals():
    u = {"id": 3, "tg_id": 99, "username": "x", "first_name": "", "last_name": "", "notify": 1, "created_at": 1.0, "web_linked_at": None}
    assert display_name(u) == "@x"
    pub = public_user(u)
    assert "tg_id" not in pub and pub["linked_web"] is False


def test_describe_device_masks_ip():
    ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    assert describe_device(ua, "203.0.113.77") == "Chrome, Windows · 203.0.*.*"
    assert "77" not in describe_device(ua, "203.0.113.77")
    assert describe_device("", "") == "браузер"


# ------------------------------------------------------------------ вход через бота
def test_login_flow_confirm_once(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "Firefox, Linux")
    assert accounts.poll_login(token, cookie) == ("pending", None)
    req = accounts.describe_login(prefix_of(token))
    assert req and req["device"] == "Firefox, Linux"
    assert accounts.answer_login(prefix_of(token), PROFILE, True) == "ok"
    status, profile = accounts.poll_login(token, cookie)
    assert status == "confirmed" and profile["id"] == 555
    assert accounts.poll_login(token, cookie) == ("expired", None)  # вход выдаётся один раз


def test_login_token_is_bound_to_starting_session(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    stranger = storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "")
    accounts.answer_login(prefix_of(token), PROFILE, True)
    assert accounts.poll_login(token, stranger) == ("expired", None)  # чужая сессия токен не получит
    assert accounts.poll_login(token, cookie)[0] == "confirmed"


def test_login_denied_and_unknown_prefix(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "")
    assert accounts.answer_login(prefix_of(token), PROFILE, False) == "denied"
    assert accounts.poll_login(token, cookie) == ("denied", None)
    assert accounts.answer_login(prefix_of(token), PROFILE, True) == "expired"  # после отказа подтвердить уже нельзя
    assert accounts.answer_login("0" * 24, PROFILE, True) == "expired"
    assert accounts.answer_login("abc", PROFILE, True) == "expired"  # слишком короткий префикс


def test_login_request_expires(accounts, storage, tmp_settings):
    tmp_settings.raw["telegram"]["login_ttl_seconds"] = 0.05
    cookie = storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "")
    time.sleep(0.12)
    assert accounts.answer_login(prefix_of(token), PROFILE, True) == "expired"
    assert accounts.poll_login(token, cookie)[0] == "expired"


def test_too_many_pending_login_requests(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    for _ in range(5):
        accounts.start_login(cookie, "")
    with pytest.raises(AccountError) as err:
        accounts.start_login(cookie, "")
    assert err.value.status == 429


def test_token_is_stored_only_as_hash(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    token = accounts.start_login(cookie, "")
    rows = storage.db.query("SELECT token_hash FROM login_requests")
    assert rows and all(token not in r["token_hash"] and len(r["token_hash"]) == 64 for r in rows)


# ------------------------------------------------------------------ сессии и данные
def test_login_rotates_cookie_and_moves_anonymous_data(accounts, storage):
    cookie = storage.sessions.create(accounts.anon_ttl)
    storage.sessions.save_state(cookie, {"state": "screening", "scenario": "transfer", "answers": {"student_name": "Иван"}})
    user, new_sid, moved = login(accounts, storage, cookie=cookie)
    assert new_sid != cookie and storage.sessions.get(cookie) is None  # старый идентификатор после входа не работает
    assert moved["dialog"] is True
    data_sid = accounts.data_sid(new_sid)
    assert data_sid != new_sid and storage.sessions.load_state(data_sid)["answers"]["student_name"] == "Иван"
    assert accounts.user_of_cookie(new_sid)["id"] == user["id"]
    assert storage.users.get(user["id"])["web_linked_at"]


def test_login_keeps_existing_account_data(accounts, storage):
    first, sid1, _ = login(accounts, storage)
    storage.sessions.save_state(accounts.data_sid(sid1), {"state": "review", "answers": {"student_name": "Из аккаунта"}})
    cookie = storage.sessions.create(accounts.anon_ttl)
    storage.sessions.save_state(cookie, {"state": "screening", "answers": {"student_name": "Анонимная"}})
    _, sid2, moved = login(accounts, storage, cookie=cookie)
    assert moved["dialog"] is False
    assert storage.sessions.load_state(accounts.data_sid(sid2))["answers"]["student_name"] == "Из аккаунта"


def test_same_account_on_two_devices_shares_data(accounts, storage):
    _, a, _ = login(accounts, storage)
    _, b, _ = login(accounts, storage)
    assert a != b and accounts.data_sid(a) == accounts.data_sid(b)


def test_logout_closes_only_this_device(accounts, storage):
    _, a, _ = login(accounts, storage)
    _, b, _ = login(accounts, storage)
    data = accounts.data_sid(a)
    accounts.logout(a)
    assert storage.sessions.get(a) is None and storage.sessions.get(b) is not None
    assert storage.sessions.get(data) is not None  # данные аккаунта остаются


def test_account_data_session_is_not_counted_as_visitor(accounts, storage):
    before = storage.sessions.count_active()
    login(accounts, storage)
    assert storage.sessions.count_active() == before + 1  # только cookie-сессия входа, но не «сессия данных»


def test_account_session_lives_longer_than_anonymous(accounts, storage):
    _, sid, _ = login(accounts, storage)
    anon = storage.sessions.create(accounts.anon_ttl)
    assert storage.sessions.get(sid)["expires_at"] - storage.sessions.get(anon)["expires_at"] > 86400 * 20
    accounts.touch_cookie(sid)
    assert storage.sessions.get(sid)["expires_at"] - time.time() > 86400 * 20  # продление не сокращает срок до суток


# ------------------------------------------------------------------ обращения
def make_ticket(storage, user_id=None):
    ticket, _ = storage.tickets.create("ref1", "transfer", "Перевод", {"request": "x", "fields": {}}, "queue", f"k{time.time()}", user_id=user_id)
    storage.tickets.attach_code(ticket["id"], "ABCD-2345")
    return ticket


def test_link_ticket_by_code(accounts, storage):
    user, _ = accounts.upsert_tg(PROFILE)
    t = make_ticket(storage)
    with pytest.raises(AccountError) as err:
        accounts.link_ticket(user, t["id"], "WRONG-0000")
    assert err.value.status == 404
    accounts.link_ticket(user, t["id"], "ABCD-2345")
    assert [x["id"] for x in storage.tickets.list_for_user(user["id"])] == [t["id"]]


def test_cannot_take_someones_ticket(accounts, storage):
    owner, _ = accounts.upsert_tg(PROFILE)
    other, _ = accounts.upsert_tg({**PROFILE, "id": 556})
    t = make_ticket(storage, owner["id"])
    with pytest.raises(AccountError) as err:
        accounts.link_ticket(other, t["id"], "ABCD-2345")
    assert err.value.status == 409
    assert storage.tickets.get_for_user(t["id"], other["id"]) is None


def test_ticket_sent_before_login_is_claimed_by_the_account(accounts, storage):
    """Обращение, отправленное из этого браузера до входа, привязывается к аккаунту: ответ деканата придёт в Telegram без ввода номера и кода."""
    cookie = storage.sessions.create(accounts.anon_ttl)
    t = make_ticket(storage)
    storage.sessions.save_state(cookie, {"state": "completed", "ticket_id": t["id"], "ticket_code": "ABCD-2345"})
    user, _, moved = login(accounts, storage, cookie=cookie)
    assert moved["ticket"] == t["id"]
    assert storage.tickets.owner_of(t["id"]) == user["id"]


def test_claiming_never_takes_a_ticket_from_another_account(accounts, storage):
    owner, _ = accounts.upsert_tg({**PROFILE, "id": 600})
    t = make_ticket(storage, owner["id"])
    cookie = storage.sessions.create(accounts.anon_ttl)
    storage.sessions.save_state(cookie, {"state": "completed", "ticket_id": t["id"]})  # чужой номер в состоянии не даёт прав
    _, _, moved = login(accounts, storage, cookie=cookie)
    assert moved["ticket"] is None and storage.tickets.owner_of(t["id"]) == owner["id"]


def test_login_without_a_ticket_claims_nothing(accounts, storage):
    _, _, moved = login(accounts, storage)
    assert moved["ticket"] is None


def test_delete_account_removes_data_but_keeps_ticket(accounts, storage):
    user, sid, _ = login(accounts, storage)
    storage.sessions.save_state(accounts.data_sid(sid), {"state": "review"})
    t = make_ticket(storage, user["id"])
    accounts.delete_account(user["id"])
    assert storage.users.get(user["id"]) is None and storage.sessions.get(sid) is None
    assert storage.tickets.get(t["id"]) is not None and storage.tickets.owner_of(t["id"]) is None


def test_purge_removes_inactive_accounts(accounts, storage, tmp_settings):
    user, _ = accounts.upsert_tg(PROFILE)
    storage.db.execute("UPDATE users SET last_seen=? WHERE id=?", (time.time() - 400 * 86400, user["id"]))
    assert storage.purge()["accounts"] == 1
    assert storage.users.get(user["id"]) is None


# ------------------------------------------------------------------ уведомления
@pytest.fixture()
def notifier(storage, tmp_settings):
    tmp_settings.raw["telegram"].update(enabled=True, bot_token="1:T")
    return Notifier(storage, tmp_settings)


class FakeAPI:
    def __init__(self, fail=None):
        self.sent, self.fail = [], fail

    def send_message(self, chat_id, text, *a, **k):
        if self.fail:
            raise self.fail
        self.sent.append((chat_id, text))
        return {"message_id": 1}


def test_ticket_reply_notifies_owner_once(accounts, storage, notifier):
    user, _ = accounts.upsert_tg(PROFILE)
    t = make_ticket(storage, user["id"])
    before = storage.tickets.get(t["id"])
    storage.tickets.update(t["id"], "done", None, "Принесите оригинал справки")
    after = storage.tickets.get(t["id"])
    notifier.ticket_changed(before, after)
    notifier.ticket_changed(before, after)  # то же событие не ставится дважды
    api = FakeAPI()
    assert notifier.dispatch_due(api) == 1
    chat, text = api.sent[0]
    assert chat == 555 and "Принесите оригинал справки" in text and f"№ {t['id']}" in text


def test_no_notification_without_change_or_owner_or_consent(accounts, storage, notifier):
    user, _ = accounts.upsert_tg(PROFILE)
    t = make_ticket(storage, user["id"])
    row = storage.tickets.get(t["id"])
    notifier.ticket_changed(row, row)
    anon = make_ticket(storage)
    storage.tickets.update(anon["id"], "done", None, "ответ")
    notifier.ticket_changed(storage.tickets.get(anon["id"]), storage.tickets.get(anon["id"]))
    storage.users.set_notify(user["id"], False)
    storage.tickets.update(t["id"], "done", None, "ответ")
    notifier.ticket_changed(row, storage.tickets.get(t["id"]))
    assert notifier.dispatch_due(FakeAPI()) == 0


def test_notifier_is_off_when_telegram_disabled(storage, tmp_settings, accounts):
    n = Notifier(storage, tmp_settings)  # telegram.enabled=false по умолчанию
    user, _ = accounts.upsert_tg(PROFILE)
    t = make_ticket(storage, user["id"])
    before = storage.tickets.get(t["id"])
    storage.tickets.update(t["id"], "done", None, "ответ")
    n.ticket_changed(before, storage.tickets.get(t["id"]))
    n.ticket_created(t, user, "web")
    assert storage.notifications.due(10) == []


def test_blocked_bot_turns_notifications_off(accounts, storage, notifier):
    from dekanat.telegram.api import TelegramError

    user, _ = accounts.upsert_tg(PROFILE)
    notifier.reminder(user, "Принести справку", time.time() - 1)
    assert notifier.dispatch_due(FakeAPI(fail=TelegramError(403, "Forbidden: bot was blocked by the user"))) == 0
    assert storage.users.get(user["id"])["notify"] == 0
    assert storage.notifications.due(10) == []  # повторять бесполезно


def test_flood_control_postpones_instead_of_failing(accounts, storage, notifier):
    from dekanat.telegram.api import TelegramError

    user, _ = accounts.upsert_tg(PROFILE)
    notifier.reminder(user, "Напоминание", time.time() - 1)
    notifier.dispatch_due(FakeAPI(fail=TelegramError(429, "Too Many Requests", retry_after=30)))
    assert storage.notifications.due(10) == []  # отложено
    row = storage.db.one("SELECT attempts, send_after FROM notifications")
    assert row["attempts"] == 0 and row["send_after"] > time.time() + 20


def test_transient_error_retries_then_gives_up(accounts, storage, notifier):
    from dekanat.telegram.api import TelegramError

    user, _ = accounts.upsert_tg(PROFILE)
    nid = notifier.reminder(user, "x", time.time() - 1)
    for _ in range(5):
        storage.db.execute("UPDATE notifications SET send_after=0 WHERE id=?", (nid,))
        notifier.dispatch_due(FakeAPI(fail=TelegramError(500, "oops")))
    storage.db.execute("UPDATE notifications SET send_after=0 WHERE id=?", (nid,))
    assert storage.notifications.due(10) == []  # после пяти попыток сообщение больше не отправляется


def test_reminders_are_limited_per_person(accounts, storage, notifier):
    user, _ = accounts.upsert_tg(PROFILE)
    ids = [notifier.reminder(user, f"n{i}", time.time() + 3600) for i in range(5)]
    assert ids[:3] != [None] * 3 and ids[3:] == [None, None]


def test_staff_chat_gets_new_ticket_message(accounts, storage, notifier, tmp_settings):
    tmp_settings.raw["telegram"]["staff_chat_id"] = "-100123"
    user, _ = accounts.upsert_tg(PROFILE)
    t = make_ticket(storage, user["id"])
    notifier.ticket_created(t, user, "web")
    notifier.ticket_created(t, user, "web")  # повтор до отправки не дублируется
    api = FakeAPI()
    notifier.dispatch_due(api)
    chats = sorted(c for c, _ in api.sent)
    assert chats == [-100123, 555]  # сотрудникам — о новом обращении, студенту — подтверждение


def test_bot_submitted_ticket_has_no_extra_ack(accounts, storage, notifier):
    """Ticket из бота: подтверждение человек уже видит в чате, второе сообщение не нужно."""
    user, _ = accounts.upsert_tg(PROFILE)
    notifier.ticket_created(make_ticket(storage, user["id"]), user, "bot")
    assert notifier.dispatch_due(FakeAPI()) == 0
