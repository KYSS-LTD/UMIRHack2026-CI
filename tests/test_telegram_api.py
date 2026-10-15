"""Клиент Bot API и фоновая служба: токен не утекает в ошибки, большие файлы не качаются, опрос продолжается после сбоев. Сеть заменена httpx.MockTransport."""

import json
import threading
import time

import httpx
import pytest

from dekanat.telegram.api import TelegramAPI, TelegramError
from dekanat.telegram.service import TelegramService, webhook_path_secret
from dekanat.web.app import create_app
from test_account_api import BOT, tg_settings
from test_telegram_auth import TOKEN


def make_api(handler):
    return TelegramAPI(TOKEN, "https://api.telegram.test", client=httpx.Client(transport=httpx.MockTransport(handler)))


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def test_token_hidden_in_repr():
    api = make_api(lambda r: ok({}))
    assert TOKEN not in repr(api) and "***" in repr(api)


def test_call_posts_json_and_drops_empty_params():
    seen = {}

    def handler(request):
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return ok({"message_id": 5})

    api = make_api(handler)
    assert api.send_message(42, "Привет", reply_markup=None)["message_id"] == 5
    assert seen["url"] == f"https://api.telegram.test/bot{TOKEN}/sendMessage"
    assert "reply_markup" not in seen["body"] and seen["body"]["parse_mode"] == "HTML" and seen["body"]["disable_web_page_preview"] is True


def test_files_go_as_multipart_with_json_markup():
    seen = {}

    def handler(request):
        seen["ctype"], seen["body"] = request.headers["content-type"], request.content
        return ok({"message_id": 1})

    make_api(handler).send_document(42, b"PK\x03\x04data", "zayavlenie.docx", "готово", {"inline_keyboard": [[{"text": "ок", "callback_data": "x"}]]})
    assert seen["ctype"].startswith("multipart/form-data") and b"zayavlenie.docx" in seen["body"] and b"PK\x03\x04data" in seen["body"]
    assert b'"callback_data"' in seen["body"]


def test_error_details_and_no_token_in_messages():
    def handler(request):
        return httpx.Response(429, json={"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 7", "parameters": {"retry_after": 7}})

    with pytest.raises(TelegramError) as err:
        make_api(handler).send_message(1, "x")
    assert err.value.code == 429 and err.value.retry_after == 7 and TOKEN not in str(err.value)


def test_network_failure_hides_url_with_token():
    def handler(request):
        raise httpx.ConnectError("failed to connect to " + str(request.url))

    with pytest.raises(TelegramError) as err:
        make_api(handler).get_me()
    assert err.value.code == 0 and TOKEN not in str(err.value) and TOKEN not in repr(err.value.__cause__)


def test_not_json_response():
    with pytest.raises(TelegramError):
        make_api(lambda r: httpx.Response(502, text="<html>Bad gateway</html>")).get_me()


def test_blocked_detection():
    assert TelegramError(403, "Forbidden: bot was blocked by the user").blocked
    assert TelegramError(400, "Bad Request: chat not found").blocked
    assert not TelegramError(400, "Bad Request: message is too long").blocked
    assert not TelegramError(500, "Internal").blocked


def test_download_checks_size_before_and_while_downloading():
    def handler(request):
        if request.url.path.endswith("/getFile"):
            body = json.loads(request.content)
            return ok({"file_path": "photos/a.jpg", "file_size": 10 if body["file_id"] == "small" else 50_000_000})
        return httpx.Response(200, content=b"x" * (200 if request.url.path.endswith("a.jpg") else 0))

    api = make_api(handler)
    assert api.download_file("small", 1000) == b"x" * 200
    with pytest.raises(TelegramError) as err:
        api.download_file("big", 1000)
    assert err.value.code == 413
    with pytest.raises(TelegramError):
        api.download_file("small", 100)  # Telegram занизил размер: лимит всё равно соблюдается при чтении


def test_menu_button_without_https_resets_to_commands():
    seen = []
    api = make_api(lambda r: (seen.append(json.loads(r.content)), ok(True))[1])
    api.set_chat_menu_button(None)
    api.set_chat_menu_button("https://x.test/?tg=1")
    assert seen[0]["menu_button"] == {"type": "commands"} and seen[1]["menu_button"]["web_app"]["url"] == "https://x.test/?tg=1"


# ------------------------------------------------------------------ служба
class PollAPI:
    """Сценарий опроса: заданные пачки обновлений, ошибки между ними."""

    def __init__(self, script):
        self.script = list(script)
        self.offsets = []
        self.done = threading.Event()

    def get_me(self):
        return {"username": "real_bot_name"}

    def delete_webhook(self):
        pass

    def get_updates(self, offset, timeout=25, allowed=None):
        self.offsets.append(offset)
        if not self.script:
            self.done.set()
            time.sleep(0.05)
            return []
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


@pytest.fixture()
def service(tmp_settings):
    tg_settings(tmp_settings, mode="polling")
    tmp_settings.raw["telegram"]["bot_username"] = None
    return create_app(tmp_settings).state.telegram


def test_polling_dispatches_and_remembers_offset(service):
    got = []
    service.api = PollAPI([[{"update_id": 10}, {"update_id": 11}], []])
    service.dispatch = got.append
    service.bot.setup = lambda: None
    service.notifier.dispatch_due = lambda api: 0
    service.start()
    assert service.api.done.wait(5)
    service.stop()
    assert [u["update_id"] for u in got] == [10, 11]
    assert service.api.offsets[:2] == [None, 12]
    assert service.storage.db.meta_get("telegram_update_offset") == "12"
    assert service.username == "real_bot_name"  # имя бота берётся из getMe, в настройках его можно не писать


def test_polling_resumes_from_saved_offset_after_restart(service):
    service.storage.db.meta_set("telegram_update_offset", "500")
    service.api = PollAPI([])
    service.dispatch = lambda u: None
    service.bot.setup = lambda: None
    service.notifier.dispatch_due = lambda api: 0
    service.start()
    assert service.api.done.wait(5)
    service.stop()
    assert service.api.offsets[0] == 500


def test_polling_survives_errors(service):
    got = []
    service.api = PollAPI([TelegramError(0, "нет связи"), [{"update_id": 1}]])
    service.dispatch = got.append
    service.bot.setup = lambda: None
    service.notifier.dispatch_due = lambda api: 0
    service.start()
    assert service.api.done.wait(8)  # после сбоя пауза 2 секунды
    service.stop()
    assert got == [{"update_id": 1}]
    assert service.status()["running"] is False


def test_status_is_safe_to_publish(service):
    st = service.status()
    assert set(st) >= {"enabled", "mode", "running", "bot", "mini_app", "error"} and TOKEN not in json.dumps(st)


def test_disabled_service_does_nothing(tmp_settings):
    svc = create_app(tmp_settings).state.telegram
    svc.start()
    assert not svc.enabled and not svc.running and svc.bot is None and svc.status()["mode"] is None


def test_enabled_without_token_stays_off(tmp_settings):
    tmp_settings.raw["telegram"]["enabled"] = True
    assert create_app(tmp_settings).state.telegram.enabled is False


def test_webhook_path_depends_on_token_and_salt():
    a = webhook_path_secret(TOKEN, None)
    assert a != webhook_path_secret(TOKEN, "salt") and a != webhook_path_secret("1:OTHER", None) and TOKEN not in a and len(a) == 32


def test_webhook_mode_registers_https_url(tmp_settings):
    app = create_app(tg_settings(tmp_settings, mode="webhook"))
    svc = app.state.telegram
    calls = []

    class Hook:
        def get_me(self):
            return {"username": BOT}

        def set_webhook(self, url, secret):
            calls.append((url, secret))

        def delete_webhook(self):
            raise AssertionError("в режиме webhook его снимать нельзя")

    svc.api = Hook()
    svc.bot.setup = lambda: None
    svc.notifier.dispatch_due = lambda api: 0
    svc._boot()
    svc.stop()
    assert calls and calls[0][0] == "https://dekanat.test" + svc.webhook_path and calls[0][1] and TOKEN not in calls[0][0]


def test_webhook_mode_requires_https(tmp_settings):
    tg_settings(tmp_settings, mode="webhook")
    tmp_settings.raw["telegram"]["public_url"] = "http://insecure.test"
    svc = create_app(tmp_settings).state.telegram
    svc.api = type("A", (), {"get_me": lambda s: {"username": BOT}, "set_webhook": lambda s, *a: (_ for _ in ()).throw(AssertionError("не должен вызываться"))})()
    svc.bot.setup = lambda: None
    svc.notifier.dispatch_due = lambda api: 0
    svc._boot()
    svc.stop()
    assert "https" in svc.error
