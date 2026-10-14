"""Tesseract «установлен, а плашка «Проверка документов недоступна» висит»: находка без перезапуска, список языков из stderr, неверный TESSDATA_PREFIX,
скачивание языковых данных и понятная подсказка человеку. Настоящий Tesseract не нужен: его вызовы подменены."""

import subprocess
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import dekanat.ocr.engine as eng
from conftest import admin_login
from dekanat.cli import main
from dekanat.ocr.engine import TesseractEngine, ocr_fix_hint
from dekanat.ocr.langdata import DEFAULT_LANGS, MIN_BYTES, LangDataError, fetch_languages
from dekanat.pipeline import Dekanat
from dekanat.web.app import create_app

LIST_OK = "List of available languages in \"C:/Program Files/Tesseract-OCR/tessdata/\" (3):\neng\nosd\nrus\n"
LIST_NO_RUS = "List of available languages in \"C:/Program Files/Tesseract-OCR/tessdata/\" (2):\neng\nosd\n"


def runner(stdout=LIST_OK, stderr="", fail_with=None, only_when=None, calls=None):
    """Подменяет subprocess.run: --version и --list-langs. only_when(env) — при каких переменных окружения список непустой."""
    def run(cmd, capture_output=True, timeout=None, env=None, **kw):
        if calls is not None:
            calls.append(cmd)
        if fail_with:
            raise fail_with
        if "--version" in cmd:
            return SimpleNamespace(stdout=b"tesseract v5.4.0\n", stderr=b"", returncode=0)
        good = only_when is None or only_when(env or {})
        return SimpleNamespace(stdout=(stdout if good else "").encode(), stderr=(stderr if good else "Error opening data file\n").encode(), returncode=0)
    return run


def engine(tmp_path, cmd="C:/Program Files/Tesseract-OCR/tesseract.exe", **kw):
    e = TesseractEngine(languages="rus+eng", **kw)
    e.cmd = cmd
    return e


# ============================================================================ подсказка
def test_fix_hint_for_every_case():
    assert ocr_fix_hint({"available": True}) == ""
    missing = ocr_fix_hint({"available": False, "cmd": "C:/t.exe", "missing_languages": ["rus"]})
    assert "rus" in missing and "python -m dekanat fetch-lang" in missing and "Russian" in missing
    notfound = ocr_fix_hint({"available": False, "missing_languages": []})
    assert "UB-Mannheim" in notfound and "TESSERACT_CMD" in notfound and "ocr.tesseract_cmd" in notfound
    assert "не запускается" in ocr_fix_hint({"available": False, "cmd": "C:/t.exe", "missing_languages": []})


# ============================================================================ диагностика движка
def test_a_working_install_is_available(monkeypatch, tmp_path):
    monkeypatch.setattr(eng.subprocess, "run", runner())
    info = engine(tmp_path).info(fresh=True)
    assert info["available"] is True and info["missing_languages"] == [] and info["fix"] == "" and set(info["languages"]) == {"eng", "osd", "rus"}


def test_the_language_list_printed_to_stderr_is_understood(monkeypatch, tmp_path):
    """Старые сборки печатают список языков в stderr: раньше из-за этого «нет языковых данных: rus, eng» при полной установке."""
    monkeypatch.setattr(eng.subprocess, "run", runner(stdout="", stderr=LIST_OK))
    assert engine(tmp_path).info(fresh=True)["available"] is True


def test_missing_russian_is_named_with_the_fix(monkeypatch, tmp_path):
    monkeypatch.setattr(eng.subprocess, "run", runner(stdout=LIST_NO_RUS))
    info = engine(tmp_path).info(fresh=True)
    assert info["available"] is False and info["missing_languages"] == ["rus"] and "fetch-lang" in info["fix"]


def test_tesseract_installed_after_the_server_started_is_found_without_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(eng, "find_tesseract", lambda explicit=None: None)
    monkeypatch.setattr(eng.subprocess, "run", runner())
    e = TesseractEngine(languages="rus+eng")
    assert e.cmd is None and e.info(fresh=True)["available"] is False
    monkeypatch.setattr(eng, "find_tesseract", lambda explicit=None: "C:/Program Files/Tesseract-OCR/tesseract.exe")  # человек поставил Tesseract, сервер не трогали
    info = e.info(fresh=True)
    assert info["available"] is True and e.cmd.endswith("tesseract.exe")


def test_a_wrong_tessdata_prefix_in_the_environment_is_healed(monkeypatch, tmp_path):
    """Языковые файлы на месте, но в окружении осталась TESSDATA_PREFIX от старых инструкций и указывает не туда: Tesseract «не видит» язык."""
    monkeypatch.setenv("TESSDATA_PREFIX", "C:/wrong/place")
    monkeypatch.setattr(eng.subprocess, "run", runner(only_when=lambda env: "TESSDATA_PREFIX" not in env))
    e = engine(tmp_path)
    info = e.info(fresh=True)
    assert info["available"] is True and e._ignore_env_tessdata is True
    assert "TESSDATA_PREFIX" not in e._env()  # дальше распознавание идёт без неверной переменной


def test_the_tessdata_folder_next_to_tesseract_is_found_when_the_prefix_is_wrong(monkeypatch, tmp_path):
    folder = tmp_path / "Tesseract-OCR"
    (folder / "tessdata").mkdir(parents=True)
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path / "wrong"))
    monkeypatch.setattr(eng.subprocess, "run", runner(only_when=lambda env: env.get("TESSDATA_PREFIX") == str(folder / "tessdata")))
    e = engine(tmp_path, cmd=str(folder / "tesseract.exe"))
    assert e.info(fresh=True)["available"] is True and e.tessdata_dir == str(folder / "tessdata")


def test_a_hanging_or_broken_tesseract_is_reported_with_the_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(eng.subprocess, "run", runner(fail_with=subprocess.TimeoutExpired("tesseract", 40)))
    info = engine(tmp_path).info(fresh=True)
    assert info["available"] is False and "не отвечает" in info["reason"] and info["fix"]


def test_info_is_cached_for_a_short_time(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(eng.subprocess, "run", runner(calls=calls))
    e = engine(tmp_path)
    e.info(fresh=True)
    n = len(calls)
    e.info()
    assert len(calls) == n  # запросы страницы не запускают tesseract заново
    e.info(fresh=True)
    assert len(calls) > n


# ============================================================================ скачивание языковых данных
def fake_download(log):
    def get(url):
        log.append(url)
        return b"\x00\x01traineddata" * 6000
    return get


def test_fetch_languages_downloads_missing_files_once(tmp_path):
    log = []
    res = fetch_languages(tmp_path / "tessdata", download=fake_download(log))
    assert set(res) == set(DEFAULT_LANGS) and all(v.startswith("скачан") for v in res.values())
    assert all((tmp_path / "tessdata" / f"{l}.traineddata").stat().st_size >= MIN_BYTES for l in DEFAULT_LANGS)
    assert not list((tmp_path / "tessdata").glob("*.part")) and all("tessdata_fast" in u for u in log)
    again = fetch_languages(tmp_path / "tessdata", download=fake_download(log))
    assert set(again.values()) == {"уже есть"} and len(log) == len(DEFAULT_LANGS)  # второй раз ничего не качается
    fetch_languages(tmp_path / "tessdata", ["rus"], force=True, download=fake_download(log))
    assert len(log) == len(DEFAULT_LANGS) + 1


@pytest.mark.parametrize("payload", [b"<html>404 Not Found</html>" * 5000, b"tiny"], ids=["html-error-page", "too-small"])
def test_a_bad_download_is_refused_and_leaves_no_file(tmp_path, payload):
    with pytest.raises(LangDataError, match="не тот ответ"):
        fetch_languages(tmp_path / "t", ["rus"], download=lambda u: payload)
    assert not (tmp_path / "t" / "rus.traineddata").exists()


def test_network_errors_and_bad_names_are_explained(tmp_path):
    def offline(url):
        raise OSError("getaddrinfo failed")
    with pytest.raises(LangDataError, match="Не удалось скачать «rus»"):
        fetch_languages(tmp_path / "t", ["rus"], download=offline)
    for bad in ("../x", "rus/../../y", "a b", ""):
        with pytest.raises(LangDataError, match="Недопустимое имя"):
            fetch_languages(tmp_path / "t", [bad], download=fake_download([]))


def test_cli_fetch_lang_reports_success_and_failure(tmp_settings, monkeypatch, capsys):
    import dekanat.config as cfg
    import dekanat.ocr.langdata as ld

    monkeypatch.setattr(cfg, "load_settings", lambda *a, **k: tmp_settings)
    monkeypatch.setattr(eng.subprocess, "run", runner())
    monkeypatch.setattr(eng, "find_tesseract", lambda explicit=None: "C:/Program Files/Tesseract-OCR/tesseract.exe")
    monkeypatch.setattr(ld, "_download", fake_download([]))
    assert main(["fetch-lang"]) == 0
    out = capsys.readouterr().out
    assert "rus: скачан" in out and "распознавание работает" in out and (tmp_settings.root / "data" / "tessdata" / "rus.traineddata").exists()

    def offline(url):
        raise OSError("нет сети")
    monkeypatch.setattr(ld, "_download", offline)
    assert main(["fetch-lang", "--force"]) == 1
    assert "Не получилось" in capsys.readouterr().out


# ============================================================================ сервис и кабинет
def test_downloaded_language_data_is_used_without_any_setting(tmp_settings):
    assert Dekanat(tmp_settings).engine.tessdata_dir in (None, "")
    folder = tmp_settings.root / "data" / "tessdata"
    folder.mkdir(parents=True)
    (folder / "rus.traineddata").write_bytes(b"x" * 100)
    assert Dekanat(tmp_settings).engine.tessdata_dir == str(folder)
    tmp_settings.raw.setdefault("ocr", {})["tessdata_dir"] = "D:/my/tessdata"
    assert Dekanat(tmp_settings).engine.tessdata_dir == "D:/my/tessdata"  # явная настройка сильнее


def test_the_cabinet_gets_the_ocr_state_with_the_fix(tmp_settings, monkeypatch):
    monkeypatch.setattr(eng.subprocess, "run", runner(stdout=LIST_NO_RUS))
    monkeypatch.setattr(eng, "find_tesseract", lambda explicit=None: "C:/Program Files/Tesseract-OCR/tesseract.exe")
    app = create_app(tmp_settings)
    c = TestClient(app)
    admin_login(c, app)
    ocr = c.get("/api/admin/info").json()["ocr"]
    assert ocr["available"] is False and ocr["missing_languages"] == ["rus"] and "fetch-lang" in ocr["fix"] and ocr["cmd"].endswith("tesseract.exe")
    assert TestClient(app).get("/api/admin/info").status_code == 401
