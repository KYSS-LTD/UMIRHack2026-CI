import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dekanat.config import Settings, load_settings  # noqa: E402
from dekanat.llm import LLMBackend, LLMConfig, LLMService  # noqa: E402
from dekanat.ocr import find_tesseract  # noqa: E402

needs_ocr = pytest.mark.skipif(find_tesseract() is None, reason="Tesseract OCR не установлен")


def make_settings(tmp: Path) -> Settings:
    """Копия конфигурации во временную папку: тесты могут менять типы документов, не трогая проект."""
    shutil.copytree(ROOT / "config", tmp / "config")
    (tmp / "data").mkdir(exist_ok=True)
    shutil.copytree(ROOT / "data" / "regulations", tmp / "data" / "regulations")
    shutil.copytree(ROOT / "data" / "samples", tmp / "data" / "samples")
    base = load_settings()
    return Settings(raw=base.raw, root=tmp)


# тестовые учётные данные: существуют только внутри тестов и во временной базе
ADMIN_USER, ADMIN_PASS = "test-admin", "Test-Pass-12345!"


def admin_login(client, app) -> None:
    """Создаёт администратора во временном хранилище приложения и входит под ним (cookie сохраняется в клиенте)."""
    app.state.auth.create_admin(ADMIN_USER, ADMIN_PASS)
    r = client.post("/api/admin/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="session")
def app():
    from dekanat.pipeline import Dekanat

    return Dekanat()


@pytest.fixture()
def tmp_settings(tmp_path):
    return make_settings(tmp_path)


@pytest.fixture(scope="session")
def samples():
    return ROOT / "data" / "samples"


@pytest.fixture(scope="session")
def synth_set(tmp_path_factory):
    """Небольшой синтетический набор: чистые сканы и лёгкие фото, по одному документу каждого типа."""
    from dekanat.tools import synth

    out = tmp_path_factory.mktemp("synth")
    synth.generate(out, n_per_type=3, levels=(0, 1), seed=21)
    return out


class FakeBackend(LLMBackend):
    """Подменяемая «модель» для тестов: отдаёт заготовленные ответы и считает вызовы. Настоящая модель в обычных тестах не нужна."""

    name = "fake"

    def __init__(self, responses=None, available=True, error=None):
        self.responses = list(responses or [])
        self.calls: list[tuple[str, str]] = []
        self._available = available
        self.error = error

    def available(self) -> bool:
        return self._available

    def complete(self, system, user, *, max_tokens, json_mode=True):
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        r = self.responses.pop(0) if self.responses else "{}"
        return r(user) if callable(r) else r


def make_llm(backend, **cfg) -> LLMService:
    """Сервис с включённой моделью и подменённым backend."""
    return LLMService(LLMConfig(enabled=True, **cfg), backend)
