"""Чистота кода: неиспользуемые импорты и переменные, необъявленные имена (pyflakes). Мусор не копится — тест падает сразу."""

from pathlib import Path

import pytest

pyflakes_api = pytest.importorskip("pyflakes.api")
from pyflakes import reporter  # noqa: E402

PKG = Path(__file__).resolve().parent.parent / "dekanat"


class _Collect(reporter.Reporter):
    def __init__(self):
        import io

        super().__init__(io.StringIO(), io.StringIO())
        self.found: list[str] = []

    def flake(self, message):
        self.found.append(f"{message.filename}:{message.lineno}: {message.message % message.message_args}")


def test_the_package_has_no_pyflakes_warnings():
    found: list[str] = []
    for path in sorted(PKG.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        c = _Collect()
        pyflakes_api.check(path.read_text(encoding="utf-8"), str(path.relative_to(PKG.parent)), c)
        found += c.found
    assert not found, "pyflakes:\n" + "\n".join(found)
