"""Обёртка над Tesseract (вызов через командную строку, без лишних зависимостей)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from ..models import Word

log = logging.getLogger(__name__)

_COMMON_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Tesseract-OCR\tesseract.exe"),
    "/usr/bin/tesseract",
    "/usr/local/bin/tesseract",
    "/opt/homebrew/bin/tesseract",
]


class OcrUnavailable(RuntimeError):
    pass


def _registry_candidates() -> list[str]:
    """Где установщик Windows записал папку Tesseract: так находится установка не в «стандартную» папку (например, D:\\Tesseract-OCR)."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return []
    out: list[str] = []
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for sub in (r"SOFTWARE\Tesseract-OCR", r"SOFTWARE\WOW6432Node\Tesseract-OCR"):
            try:
                with winreg.OpenKey(root, sub) as key:
                    for name in ("InstallDir", "Path"):
                        try:
                            value, _ = winreg.QueryValueEx(key, name)
                            out.append(str(Path(str(value)) / "tesseract.exe"))
                        except OSError:
                            continue
            except OSError:
                continue
    return out


def ocr_fix_hint(info: dict) -> str:
    """Что делать, если распознавание не работает: человеческим языком, с точными шагами. Пусто — всё в порядке."""
    if info.get("available"):
        return ""
    missing = info.get("missing_languages") or []
    if info.get("cmd") and missing:
        return (f"Tesseract найден, но нет языковых данных: {', '.join(missing)}. Самый простой способ — выполнить в папке проекта команду «python -m dekanat fetch-lang» "
                "(она скачает их в папку data/tessdata, права администратора не нужны) и перезапустить сервер. Либо запустите установщик Tesseract ещё раз и в разделе "
                "«Additional language data» отметьте Russian.")
    if not info.get("cmd"):
        return ("Tesseract не найден. Установите его (Windows: установщик UB Mannheim, https://github.com/UB-Mannheim/tesseract/wiki; не забудьте отметить Russian в разделе "
                "«Additional language data») и перезапустите сервер. Если он уже установлен, укажите путь к tesseract.exe в config/settings.yaml (ocr.tesseract_cmd) "
                "или в переменной окружения TESSERACT_CMD.")
    return "Tesseract найден, но не запускается. Проверьте путь в config/settings.yaml (ocr.tesseract_cmd) и запустите «python -m dekanat doctor» для подробностей."


def find_tesseract(explicit: Optional[str] = None) -> Optional[str]:
    for cand in (explicit, os.environ.get("TESSERACT_CMD")):
        if cand and Path(cand).exists():
            return str(cand)
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in [*_COMMON_PATHS, *_registry_candidates()]:
        if cand and Path(cand).exists():
            return cand
    return None


class TesseractEngine:
    name = "tesseract"

    def __init__(self, cmd: Optional[str] = None, languages: str = "rus+eng", tessdata_dir: Optional[str] = None, timeout: int = 180):
        self._explicit = cmd
        self.cmd = find_tesseract(cmd)
        self._ignore_env_tessdata = False  # неверный TESSDATA_PREFIX в окружении: игнорируется после самопроверки (см. _collect_info)
        self.languages = languages
        self.tessdata_dir = tessdata_dir
        self.timeout = timeout
        self._version: Optional[str] = None
        self._info_cache: Optional[tuple[float, dict]] = None

    # ------------------------------------------------------------------ сервис
    def _env(self) -> dict:
        env = dict(os.environ)
        if self.tessdata_dir:
            env["TESSDATA_PREFIX"] = str(self.tessdata_dir)
        elif self._ignore_env_tessdata:
            env.pop("TESSDATA_PREFIX", None)
        return env

    def _ensure_cmd(self) -> None:
        """Tesseract могли установить уже после запуска сервера: ищем снова, перезапуск не нужен."""
        if not self.cmd:
            self.cmd = find_tesseract(self._explicit)

    def _list_languages(self, env: dict) -> list[str]:
        """Языки, которые видит Tesseract. Список печатается в stdout (5.x) или в stderr (старые сборки) — читаем оба."""
        out = subprocess.run([self.cmd, "--list-langs"], capture_output=True, timeout=40, env=env)
        text = (out.stdout or b"").decode("utf-8", "replace") + "\n" + (out.stderr or b"").decode("utf-8", "replace")
        return [l.strip() for l in text.splitlines() if re.fullmatch(r"[A-Za-z_]{2,16}", l.strip())]

    def _run(self, args: list[str], image: Optional[np.ndarray] = None) -> bytes:
        self._ensure_cmd()
        if not self.cmd:
            raise OcrUnavailable(
                "Tesseract не найден. Установите его (Windows: https://github.com/UB-Mannheim/tesseract/wiki, "
                "Linux: apt install tesseract-ocr tesseract-ocr-rus) или укажите путь в TESSERACT_CMD / config/settings.yaml."
            )
        with tempfile.TemporaryDirectory(prefix="dekanat_", ignore_cleanup_errors=True) as tmp:
            cmd = [self.cmd]
            if image is not None:
                path = Path(tmp) / "page.png"
                ok, buf = cv2.imencode(".png", image)
                if not ok:
                    raise OcrUnavailable("Не удалось подготовить изображение для OCR")
                path.write_bytes(buf.tobytes())
                cmd.append(str(path))
                cmd.append("stdout")
            cmd += args
            kwargs = {}
            if sys.platform == "win32":
                kwargs["creationflags"] = 0x08000000  # CREATE_NO_WINDOW
            try:
                proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout, env=self._env(), **kwargs)
            except subprocess.TimeoutExpired as exc:
                raise OcrUnavailable(f"Tesseract не уложился в {self.timeout} с") from exc
            if proc.returncode != 0:
                err = proc.stderr.decode("utf-8", "replace").strip()
                raise OcrUnavailable(f"Tesseract завершился с ошибкой ({proc.returncode}): {err[:400]}")
            return proc.stdout

    def _heal_tessdata(self, wanted: list[str]) -> Optional[list[str]]:
        """Языковые файлы на месте, а Tesseract их «не видит»: чаще всего виновата переменная TESSDATA_PREFIX, оставшаяся от старых инструкций
        (указывает не туда). Пробуем без неё и с папкой tessdata рядом с tesseract.exe; рабочий вариант запоминаем — OCR дальше идёт с ним."""
        trials: list[tuple[str, dict]] = []
        if os.environ.get("TESSDATA_PREFIX") and not self.tessdata_dir:
            trials.append(("env", {k: v for k, v in os.environ.items() if k != "TESSDATA_PREFIX"}))
        beside = Path(self.cmd).parent / "tessdata"
        if beside.exists():
            trials.append(("dir", {**os.environ, "TESSDATA_PREFIX": str(beside)}))
        for kind, env in trials:
            try:
                langs = self._list_languages(env)
            except Exception:
                continue
            if not [l for l in wanted if l not in langs]:
                if kind == "env":
                    self._ignore_env_tessdata = True
                else:
                    self.tessdata_dir = str(beside)
                log.warning("Tesseract не видел языки из-за TESSDATA_PREFIX=%r: используем %s", os.environ.get("TESSDATA_PREFIX"), "окружение без неё" if kind == "env" else str(beside))
                return langs
        return None

    def _tessdata_folder(self) -> str:
        """Где Tesseract ищет языковые данные (для подсказки человеку)."""
        if self.tessdata_dir:
            return str(self.tessdata_dir)
        if os.environ.get("TESSDATA_PREFIX"):
            return os.environ["TESSDATA_PREFIX"]
        guess = Path(self.cmd).parent / "tessdata" if self.cmd else None
        return str(guess) if guess and guess.exists() else ""

    def info(self, fresh: bool = False) -> dict:
        """Диагностика для `doctor`, кабинета и /api/health (результат помнится 30 с: проверка запускает tesseract дважды)."""
        if not fresh and self._info_cache and time.time() - self._info_cache[0] < 30:
            return self._info_cache[1]
        data = self._collect_info()
        data["fix"] = ocr_fix_hint(data)
        self._info_cache = (time.time(), data)
        return data

    def _collect_info(self) -> dict:
        self._ensure_cmd()
        if not self.cmd:
            return {"available": False, "reason": "tesseract не найден", "missing_languages": []}
        wanted = self.languages.split("+")
        try:
            out = subprocess.run([self.cmd, "--version"], capture_output=True, timeout=40, env=self._env())
            text = ((out.stdout or b"") + (out.stderr or b"")).decode("utf-8", "replace").strip()
            self._version = text.splitlines()[0].strip() if text else "?"
            lang_list = self._list_languages(self._env())
            if [l for l in wanted if l not in lang_list]:
                lang_list = self._heal_tessdata(wanted) or lang_list
        except Exception as exc:
            return {"available": False, "cmd": self.cmd, "reason": f"Tesseract не отвечает: {exc}", "missing_languages": []}
        need = [l for l in wanted if l not in lang_list]
        return {"available": not need, "cmd": self.cmd, "version": self._version, "languages": [l for l in lang_list if l in ("rus", "eng", "osd")], "missing_languages": need,
                "tessdata": self._tessdata_folder(), "reason": f"нет языковых данных: {', '.join(need)}" if need else ""}

    # ------------------------------------------------------------------ распознавание
    def recognize(self, image: np.ndarray, psm: int = 6) -> list[Word]:
        args = ["-l", self.languages, "--oem", "1", "--psm", str(psm), "--dpi", "300", "-c", "preserve_interword_spaces=1", "tsv"]
        raw = self._run(args, image).decode("utf-8", "replace")
        words: list[Word] = []
        for line in raw.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12 or parts[0] != "5":
                continue
            text = parts[11].strip()
            try:
                conf = float(parts[10])
                left, top, wd, ht = (float(parts[i]) for i in (6, 7, 8, 9))
            except ValueError:
                continue
            if not text or conf < 0 or wd <= 1 or ht <= 1:
                continue
            words.append(Word(text, conf, left, top, left + wd, top + ht))
        return words

    def detect_orientation(self, image: np.ndarray) -> tuple[int, float]:
        """OSD Tesseract: на сколько градусов по часовой стрелке повернуть изображение. -> (градусы, уверенность)."""
        try:
            raw = self._run(["--psm", "0", "-l", "osd"], image).decode("utf-8", "replace")
        except OcrUnavailable:
            return 0, 0.0
        rot = re.search(r"Rotate:\s*(\d+)", raw)
        conf = re.search(r"Orientation confidence:\s*([\d.]+)", raw)
        return (int(rot.group(1)) if rot else 0), (float(conf.group(1)) if conf else 0.0)
