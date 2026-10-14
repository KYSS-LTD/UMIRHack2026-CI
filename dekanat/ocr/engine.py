"""Обёртка над Tesseract (вызов через командную строку, без лишних зависимостей)."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def find_tesseract(explicit: Optional[str] = None) -> Optional[str]:
    for cand in (explicit, os.environ.get("TESSERACT_CMD")):
        if cand and Path(cand).exists():
            return str(cand)
    found = shutil.which("tesseract")
    if found:
        return found
    for cand in _COMMON_PATHS:
        if cand and Path(cand).exists():
            return cand
    return None


class TesseractEngine:
    name = "tesseract"

    def __init__(self, cmd: Optional[str] = None, languages: str = "rus+eng", tessdata_dir: Optional[str] = None, timeout: int = 180):
        self.cmd = find_tesseract(cmd)
        self.languages = languages
        self.tessdata_dir = tessdata_dir
        self.timeout = timeout
        self._version: Optional[str] = None

    # ------------------------------------------------------------------ сервис
    def _env(self) -> dict:
        env = dict(os.environ)
        if self.tessdata_dir:
            env["TESSDATA_PREFIX"] = str(self.tessdata_dir)
        return env

    def _run(self, args: list[str], image: Optional[np.ndarray] = None) -> bytes:
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

    def info(self) -> dict:
        """Диагностика для `doctor` и /api/health."""
        if not self.cmd:
            return {"available": False, "reason": "tesseract не найден"}
        try:
            out = subprocess.run([self.cmd, "--version"], capture_output=True, timeout=20, env=self._env())
            text = (out.stdout or out.stderr).decode("utf-8", "replace")
            self._version = text.splitlines()[0].strip() if text else "?"
            langs = subprocess.run([self.cmd, "--list-langs"], capture_output=True, timeout=20, env=self._env())
            lang_list = [l.strip() for l in langs.stdout.decode("utf-8", "replace").splitlines()[1:] if l.strip()]
        except Exception as exc:  # pragma: no cover
            return {"available": False, "reason": str(exc)}
        need = [l for l in self.languages.split("+") if l not in lang_list]
        return {"available": not need, "cmd": self.cmd, "version": self._version, "languages": [l for l in lang_list if l in ("rus", "eng", "osd")], "missing_languages": need,
                "reason": f"нет языковых данных: {', '.join(need)}" if need else ""}

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
