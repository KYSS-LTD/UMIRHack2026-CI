"""Загрузка настроек и описаний типов документов (YAML) с проверкой корректности."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

ROOT = Path(os.environ.get("DEKANAT_HOME") or Path(__file__).resolve().parent.parent)

# Главный переключатель языковой модели (значение по умолчанию). Переопределяется ключом USE_LOCAL_LLM
# в config/settings.yaml или переменной окружения USE_LOCAL_LLM. Читается только здесь — через Settings.use_local_llm.
USE_LOCAL_LLM = False

FIELD_KINDS = {"person", "org", "text", "date", "number", "choice", "period", "grade", "group", "course", "composite", "marks", "mark"}
FALLBACKS = {"header_org", "unclaimed_date_last"}

_DATE_RE = (
    r"(?:\d{1,2}\s*[./\-]\s*\d{1,2}\s*[./\-]\s*(?:\d{4}|\d{2})(?!\d)"
    r"|[«\"“]?\s*\d{1,2}\s*[»\"”]?\s*(?:января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)\s+\d{4})"
)
_FIO_RE = (
    r"(?:[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?\s+[А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+)?"
    r"|[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?\s+[А-ЯЁ]{2,}(?:\s+[А-ЯЁ]{2,})?"
    r"|[А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.)"
)
_NUM_RE = r"(?:[A-Za-zА-Яа-я0-9][A-Za-zА-Яа-я0-9/\-]*\d[A-Za-zА-Яа-я0-9/\-]*)"
MACROS = {"{DATE}": _DATE_RE, "{FIO}": _FIO_RE, "{NUM}": _NUM_RE}


class ConfigError(ValueError):
    pass


def expand_macros(pattern: str) -> str:
    for key, value in MACROS.items():
        pattern = pattern.replace(key, value)
    return pattern


@dataclass
class PartSpec:
    id: str
    title: str
    kind: str
    labels: list[str] = field(default_factory=list)
    mention: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    mark: Optional[str] = None
    missing_phrase: str = ""


@dataclass
class FieldSpec:
    id: str
    title: str
    kind: str
    labels: list[str] = field(default_factory=list)
    mention: list[str] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    choices: dict[str, list[str]] = field(default_factory=dict)
    fallback: Optional[str] = None
    multiline: bool = False
    parts: list[PartSpec] = field(default_factory=list)
    missing_phrase: str = ""
    format: str = ""
    export_title: str = ""  # название поля в results.json (по умолчанию title)
    # только для kind=period: подписи, по которым дата начала и дата окончания стоят отдельными строками («Начало периода обучения: …»)
    start_labels: list[str] = field(default_factory=list)
    end_labels: list[str] = field(default_factory=list)

    def part(self, part_id: str) -> Optional[PartSpec]:
        return next((p for p in self.parts if p.id == part_id), None)


@dataclass
class DocTypeSpec:
    id: str
    title: str
    short_title: str = ""
    genitive: str = ""
    aliases: list[str] = field(default_factory=list)
    keywords: list[tuple[str, float]] = field(default_factory=list)
    negative: list[tuple[str, float]] = field(default_factory=list)
    fields: list[FieldSpec] = field(default_factory=list)
    required_fallback: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    source: str = ""
    # соответствие формату results.json организаторов (необязательно): {"type": id типа у организаторов, "fields": [{unit, key, title, part?, mark?}]}
    official: dict = field(default_factory=dict)

    def field_by_id(self, field_id: str) -> Optional[FieldSpec]:
        return next((f for f in self.fields if f.id == field_id), None)

    def iter_units(self):
        """Все «единицы проверки»: (поле, часть|None). Составное поле раскладывается на части."""
        for f in self.fields:
            if f.parts:
                for p in f.parts:
                    yield f, p
            else:
                yield f, None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _compile_check(pattern: str, where: str) -> None:
    try:
        re.compile(expand_macros(pattern))
    except re.error as exc:
        raise ConfigError(f"{where}: некорректное регулярное выражение «{pattern}»: {exc}") from exc


def _parse_keywords(items: Any, where: str) -> list[tuple[str, float]]:
    out = []
    for item in _as_list(items):
        if isinstance(item, str):
            out.append((item, 1.0))
        elif isinstance(item, dict) and "phrase" in item:
            out.append((str(item["phrase"]), float(item.get("weight", 1))))
        else:
            raise ConfigError(f"{where}: ключевое слово должно быть строкой или {{phrase, weight}}, получено {item!r}")
    return out


_ID_RE = re.compile(r"[a-z][a-z0-9_]*")


def _parse_part(raw: dict, where: str) -> PartSpec:
    if not isinstance(raw, dict) or "id" not in raw:
        raise ConfigError(f"{where}: у части поля обязателен id")
    if not _ID_RE.fullmatch(str(raw["id"])):
        raise ConfigError(f"{where}: id части «{raw['id']}» должен состоять из латинских строчных букв, цифр и «_»")
    kind = raw.get("kind", "text")
    if kind not in FIELD_KINDS or kind in ("composite", "marks"):
        raise ConfigError(f"{where}/{raw['id']}: недопустимый kind «{kind}» для части")
    if kind == "mark" and raw.get("mark") not in ("signature", "stamp"):
        raise ConfigError(f"{where}/{raw['id']}: для kind=mark нужно mark: signature | stamp")
    spec = PartSpec(
        id=str(raw["id"]),
        title=str(raw.get("title", raw["id"])),
        kind=kind,
        labels=[str(x) for x in _as_list(raw.get("labels"))],
        mention=[str(x) for x in _as_list(raw.get("mention"))],
        patterns=[str(x) for x in _as_list(raw.get("patterns"))],
        mark=raw.get("mark"),
        missing_phrase=str(raw.get("missing_phrase", "")),
    )
    for pat in spec.patterns:
        _compile_check(pat, f"{where}/{spec.id}")
    return spec


def _parse_field(raw: dict, where: str) -> FieldSpec:
    if not isinstance(raw, dict) or "id" not in raw:
        raise ConfigError(f"{where}: у поля обязателен id")
    fid = str(raw["id"])
    if not _ID_RE.fullmatch(fid):
        raise ConfigError(f"{where}: id поля «{fid}» должен состоять из латинских строчных букв, цифр и «_»")
    kind = raw.get("kind", "text")
    if kind not in FIELD_KINDS or kind == "mark":
        raise ConfigError(f"{where}/{fid}: недопустимый kind «{kind}». Допустимы: {sorted(FIELD_KINDS - {'mark'})}")
    fallback = raw.get("fallback")
    if fallback and fallback not in FALLBACKS:
        raise ConfigError(f"{where}/{fid}: неизвестный fallback «{fallback}». Допустимы: {sorted(FALLBACKS)}")
    spec = FieldSpec(
        id=fid,
        title=str(raw.get("title", fid)),
        kind=kind,
        labels=[str(x) for x in _as_list(raw.get("labels"))],
        mention=[str(x) for x in _as_list(raw.get("mention"))],
        patterns=[str(x) for x in _as_list(raw.get("patterns"))],
        choices={str(k): [str(x) for x in _as_list(v)] for k, v in (raw.get("choices") or {}).items()},
        fallback=fallback,
        multiline=bool(raw.get("multiline", False)),
        parts=[_parse_part(p, f"{where}/{fid}") for p in _as_list(raw.get("parts"))],
        missing_phrase=str(raw.get("missing_phrase", "")),
        format=str(raw.get("format", "")),
        export_title=str(raw.get("export_title", "")),
        start_labels=[str(x) for x in _as_list(raw.get("start_labels"))],
        end_labels=[str(x) for x in _as_list(raw.get("end_labels"))],
    )
    if (spec.start_labels or spec.end_labels) and kind != "period":
        raise ConfigError(f"{where}/{fid}: start_labels и end_labels есть только у kind=period")
    if kind in ("composite", "marks") and not spec.parts:
        raise ConfigError(f"{where}/{fid}: для kind={kind} нужно описать parts")
    if kind == "choice" and not spec.choices:
        raise ConfigError(f"{where}/{fid}: для kind=choice нужно описать choices")
    if not spec.labels and not spec.patterns and not spec.fallback and not spec.parts:
        raise ConfigError(f"{where}/{fid}: нет ни labels, ни patterns, ни fallback — поле нечем искать")
    ids = [p.id for p in spec.parts]
    if len(ids) != len(set(ids)):
        raise ConfigError(f"{where}/{fid}: повторяющиеся id частей")
    for pat in spec.patterns:
        _compile_check(pat, f"{where}/{fid}")
    return spec


def _parse_official(raw: Any, where: str) -> dict:
    """Блок official: как поля этого типа называются в results.json организаторов.

        official:
          type: study_certificate        # id типа у организаторов (по умолчанию — id этого типа)
          fields:
            - {unit: student_name, key: student_name, title: ФИО студента}
            - {unit: study_period, part: start, key: study_start, title: Начало периода обучения}   # период -> start / end
            - {unit: seal_signature.stamp, key: seal_present, title: Печать вуза, mark: true}        # отметка -> true / false
    """
    if not raw:
        return {}
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), list) or not raw["fields"]:
        raise ConfigError(f"{where}: official должен быть словарём с непустым списком fields")
    fields = []
    for i, item in enumerate(raw["fields"], 1):
        if not isinstance(item, dict) or not {"unit", "key", "title"} <= set(item):
            raise ConfigError(f"{where}: official.fields[{i}] должен содержать unit, key и title")
        part = item.get("part")
        if part not in (None, "start", "end"):
            raise ConfigError(f"{where}: official.fields[{i}].part — start или end")
        fields.append({"unit": str(item["unit"]), "key": str(item["key"]), "title": str(item["title"]), "part": part, "mark": bool(item.get("mark", False))})
    keys = [f["key"] for f in fields]
    if len(keys) != len(set(keys)):
        raise ConfigError(f"{where}: official.fields — повторяющиеся key")
    return {"type": str(raw.get("type") or ""), "fields": fields}


def parse_doc_type(raw: dict, source: str = "") -> DocTypeSpec:
    where = source or "тип документа"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: ожидался словарь YAML")
    for key in ("id", "title", "fields"):
        if key not in raw:
            raise ConfigError(f"{where}: отсутствует обязательный ключ «{key}»")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", str(raw["id"])):
        raise ConfigError(f"{where}: id должен состоять из латинских строчных букв, цифр и «_»")
    classify = raw.get("classify") or {}
    spec = DocTypeSpec(
        id=str(raw["id"]),
        title=str(raw["title"]),
        short_title=str(raw.get("short_title") or raw["title"]),
        genitive=str(raw.get("genitive") or raw["title"]),
        aliases=[str(x) for x in _as_list(raw.get("aliases"))] or [str(raw["title"])],
        keywords=_parse_keywords(classify.get("keywords"), where),
        negative=_parse_keywords(classify.get("negative"), where),
        fields=[_parse_field(f, where) for f in _as_list(raw["fields"])],
        required_fallback=[str(x) for x in _as_list(raw.get("required_fallback"))],
        checks=[dict(c) for c in _as_list(raw.get("checks"))],
        source=source,
        official=_parse_official(raw.get("official"), where),
    )
    if not spec.fields:
        raise ConfigError(f"{where}: пустой список полей")
    ids = [f.id for f in spec.fields]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise ConfigError(f"{where}: повторяющиеся id полей: {dup}")
    units = {f"{f.id}.{p.id}" if p else f.id for f, p in spec.iter_units()}
    for ref in spec.required_fallback:
        if ref not in units and ref not in ids:
            raise ConfigError(f"{where}: required_fallback ссылается на неизвестное поле «{ref}»")
    for item in spec.official.get("fields", []):
        if item["unit"] not in units:
            raise ConfigError(f"{where}: official ссылается на неизвестную единицу «{item['unit']}»")
    for chk in spec.checks:
        if chk.get("type") != "date_order" or not {"before", "after"} <= set(chk):
            raise ConfigError(f"{where}: проверка должна иметь вид {{type: date_order, before: <поле>, after: <поле>, message: ...}}")
        for key in ("before", "after"):
            if chk[key] not in units and chk[key] not in ids:
                raise ConfigError(f"{where}: checks ссылается на неизвестное поле «{chk[key]}»")
    return spec


def parse_doc_type_yaml(text: str, source: str = "") -> DocTypeSpec:
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{source or 'YAML'}: ошибка синтаксиса YAML: {exc}") from exc
    return parse_doc_type(raw, source)


def load_doc_types(directory: Path) -> dict[str, DocTypeSpec]:
    result: dict[str, DocTypeSpec] = {}
    if not directory.exists():
        raise ConfigError(f"Папка с типами документов не найдена: {directory}")
    for path in sorted(directory.glob("*.y*ml")):
        spec = parse_doc_type_yaml(path.read_text(encoding="utf-8"), path.name)
        if spec.id in result:
            raise ConfigError(f"{path.name}: id «{spec.id}» уже используется в {result[spec.id].source}")
        result[spec.id] = spec
    if not result:
        raise ConfigError(f"В {directory} нет ни одного описания типа документа (*.yaml)")
    return result


@dataclass
class Settings:
    raw: dict[str, Any]
    root: Path = ROOT

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return default if node is None else node

    def path(self, dotted: str, default: str = "") -> Path:
        value = Path(str(self.get(dotted, default)))
        return value if value.is_absolute() else self.root / value

    @property
    def doc_types_dir(self) -> Path:
        return self.path("doc_types_dir", "config/doc_types")

    @property
    def use_local_llm(self) -> bool:
        """USE_LOCAL_LLM — единственное место, где читается главный переключатель языковой модели.

        False (по умолчанию): ни один компонент не обращается к модели, она не загружается.
        Остальной код спрашивает у `LLMService.enabled`, а не читает настройку сам."""
        value = self.raw.get("USE_LOCAL_LLM")
        return _as_bool(USE_LOCAL_LLM if value is None else value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "да")
    return bool(value)


def _apply_env(raw: dict, prefix: str = "DEKANAT") -> None:
    for key, value in list(raw.items()):
        env_name = f"{prefix}_{key}".upper()
        if isinstance(value, dict):
            _apply_env(value, env_name)
            continue
        env = os.environ.get(env_name)
        if env is None:
            continue
        if isinstance(value, bool):
            raw[key] = env.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(value, int):
            raw[key] = int(env)
        elif isinstance(value, float):
            raw[key] = float(env)
        else:
            raw[key] = env if env.lower() not in ("", "none", "null") else None


def load_settings(path: Optional[Path] = None) -> Settings:
    path = path or Path(os.environ.get("DEKANAT_CONFIG") or ROOT / "config" / "settings.yaml")
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    # ключи с null в YAML тоже должны переопределяться окружением
    for section, keys in (("ocr", ("tesseract_cmd", "tessdata_dir")),):
        raw.setdefault(section, {})
        for k in keys:
            raw[section].setdefault(k, None)
    _apply_env(raw)
    # переключатель модели задаётся переменной с тем же именем, что и параметр: USE_LOCAL_LLM=true
    plain = os.environ.get("USE_LOCAL_LLM")
    if plain is not None:
        raw["USE_LOCAL_LLM"] = _as_bool(plain)
    return Settings(raw=raw, root=ROOT)
