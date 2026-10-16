"""Общие помощники репозиториев: JSON-колонки и хэши."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Optional


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _loads(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def sha256_hex(data: "bytes | str") -> str:
    return hashlib.sha256(data.encode("utf-8") if isinstance(data, str) else data).hexdigest()


def session_ref(sid: str) -> str:
    """Обезличенный идентификатор сессии для статистики и обращений: по нему нельзя получить сам cookie."""
    return sha256_hex("dekanat-session:" + sid)[:16]
