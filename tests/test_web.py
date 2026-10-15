import pytest
from fastapi.testclient import TestClient

from conftest import admin_login, needs_ocr
from dekanat.web.app import create_app


@pytest.fixture()
def client(tmp_settings):
    """Клиент, вошедший как администратор: правка Положения и типов документов требует входа."""
    app = create_app(tmp_settings)
    c = TestClient(app)
    admin_login(c, app)
    return c


@pytest.fixture()
def anon(tmp_settings):
    return TestClient(create_app(tmp_settings))


def test_index_and_static(client):
    import re

    r = client.get("/")
    assert r.status_code == 200 and "Онлайн-деканат" in r.text and 'id="root"' in r.text
    assets = re.findall(r'(/static/app/assets/[^"]+)', r.text)
    assert any(a.endswith(".js") for a in assets) and any(a.endswith(".css") for a in assets)
    for a in assets:
        assert client.get(a).status_code == 200


def test_health_and_doctypes(client):
    h = client.get("/api/health").json()
    assert h["regulation"]["clauses"] == 25 and len(h["doc_types"]) == 3 and h["allow_edit"] is True
    types = client.get("/api/doctypes").json()
    study = next(t for t in types if t["id"] == "study_certificate")
    assert study["examples"] and any(f["id"] == "issue" and len(f["parts"]) == 2 for f in study["fields"])


@needs_ocr
def test_analyze_end_to_end(client, samples):
    data = (samples / "sample_02.png").read_bytes()
    r = client.post("/api/analyze", files={"file": ("sample_02.png", data, "image/png")},
                    data={"question": "Хватит ли этой справки для перевода на 2 курс?", "doc_type": "auto"})
    assert r.status_code == 200
    j = r.json()
    assert j["doc_type"]["id"] == "study_certificate" and j["answer"]["verdict"] == "missing"
    assert j["answer"]["sources"][0]["label"] == "п. 3.4"
    assert j["images"][0]["data_url"].startswith("data:image/jpeg;base64,") and j["images"][0]["width"] > 500
    assert j["export"]["fields"]["ФИО студента"] != "не найдено" and j["export"]["verdict"] == "missing"


def test_rule_question_without_file(client):
    r = client.post("/api/analyze", data={"question": "Сколько дней рассматривается заявление?", "doc_type": "auto"})
    assert r.status_code == 200 and r.json()["answer"]["sources"][0]["ref"] == "6.1"


def test_input_validation(client):
    assert client.post("/api/analyze", data={"question": "", "doc_type": "auto"}).status_code == 400
    assert client.post("/api/analyze", files={"file": ("x.png", b"", "image/png")}, data={"question": "?"}).status_code == 400
    r = client.post("/api/analyze", files={"file": ("x.jpg", b"not an image", "image/jpeg")}, data={"question": "Что не так?"})
    assert r.status_code == 400 and "Не удалось прочитать" in r.json()["detail"]


def test_regulation_can_be_replaced(client):
    assert len(client.get("/api/regulation").json()["clauses"]) == 25
    text = "## 1. Тест\n1.1. Деканат выдаёт справки в течение 7 рабочих дней.\n1.2. Заявления рассматривает декан.\n"
    r = client.put("/api/regulation", files={"file": ("новое.md", text.encode("utf-8"), "text/markdown")})
    assert r.status_code == 200 and r.json()["clauses"] == 2
    a = client.post("/api/analyze", data={"question": "В течение какого времени выдают справки?"}).json()["answer"]
    assert a["verdict"] == "ok" and a["sources"][0]["ref"] == "1.1" and "7 рабочих дней" in a["text"]
    assert client.put("/api/regulation", files={"file": ("x.exe", b"MZ", "application/octet-stream")}).status_code == 400
    assert client.put("/api/regulation", files={"file": ("пусто.txt", b"   ", "text/plain")}).status_code == 400


def test_doctype_editor_roundtrip(client):
    raw = client.get("/api/doctypes/study_certificate/yaml").text
    assert "id: study_certificate" in raw
    bad = raw.replace("kind: person", "kind: wizard", 1)
    v = client.post("/api/doctypes/validate", content=bad, headers={"Content-Type": "text/plain"}).json()
    assert v["ok"] is False and "wizard" in v["error"]
    assert client.put("/api/doctypes/study_certificate", content=bad, headers={"Content-Type": "text/plain"}).status_code == 400
    assert client.get("/api/doctypes").json()  # после неудачного сохранения сервис цел

    tpl = client.get("/api/doctypes/template").text
    assert client.post("/api/doctypes/validate", content=tpl, headers={"Content-Type": "text/plain"}).json()["ok"] is True
    r = client.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"})
    assert r.status_code == 200 and r.json()["id"] == "my_document"
    assert any(t["id"] == "my_document" for t in client.get("/api/doctypes").json())
    assert client.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"}).status_code == 400  # уже есть
    assert client.delete("/api/doctypes/my_document").status_code == 200
    assert not any(t["id"] == "my_document" for t in client.get("/api/doctypes").json())


def test_editing_can_be_disabled(tmp_settings):
    tmp_settings.raw["server"]["allow_edit"] = False
    c = TestClient(create_app(tmp_settings))
    tpl = c.get("/api/doctypes/template").text
    assert c.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"}).status_code == 403
    assert c.put("/api/regulation", files={"file": ("a.txt", b"1.1. x", "text/plain")}).status_code == 403
    assert c.delete("/api/doctypes/study_certificate").status_code == 403


def test_samples_are_served_safely(client):
    items = client.get("/api/samples").json()
    assert len(items) == 10 and client.get(f"/api/samples/{items[0]['file']}").status_code == 200
    assert client.get("/api/samples/..%2Fmanifest.json").status_code == 404
    assert client.get("/api/samples/secret.txt").status_code == 404


def test_editing_requires_admin_login(anon):
    tpl = anon.get("/api/doctypes/template").text
    text = "1.1. Деканат выдаёт справки в течение 7 рабочих дней."
    assert anon.put("/api/regulation", files={"file": ("a.md", text.encode(), "text/markdown")}).status_code == 401
    assert anon.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"}).status_code == 401
    assert anon.delete("/api/doctypes/study_certificate").status_code == 401
    assert len(anon.get("/api/regulation").json()["clauses"]) == 25  # чтение остаётся открытым
    r = anon.get("/api/regulation").json()
    assert r["editable"] is False and anon.get("/api/health").json()["can_edit"] is False


def test_logged_in_admin_sees_editing_enabled(client):
    assert client.get("/api/regulation").json()["editable"] is True
    h = client.get("/api/health").json()
    assert h["can_edit"] is True and h["allow_edit"] is True and h["admin"]["authenticated"] is True


def test_legacy_open_mode_can_be_enabled_for_local_use(tmp_settings):
    tmp_settings.raw["admin"]["protect_edit"] = False
    c = TestClient(create_app(tmp_settings))
    tpl = c.get("/api/doctypes/template").text
    assert c.put("/api/doctypes/new", content=tpl, headers={"Content-Type": "text/plain"}).status_code == 200
