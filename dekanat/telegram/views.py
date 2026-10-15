"""Оформление сообщений бота: ответ с источниками, карточка полей, обращения, клавиатуры. Чистые функции без обращений к сети.

Текст идёт в режиме HTML: всё, что пришло от пользователя или из документа, экранируется через esc().
"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any, Optional

STATUS_TITLES = {"new": "Принято, ждёт рассмотрения", "in_progress": "Рассматривается", "done": "Рассмотрено", "rejected": "Отклонено"}
STATUS_EMOJI = {"new": "🆕", "in_progress": "🔄", "done": "✅", "rejected": "❌"}
LIMIT = 3900  # потолок сообщения Telegram — 4096 знаков; запас на разметку

MENU = {
    "📄 Проверить документ": "check",
    "❓ Вопрос по Положению": "ask",
    "📝 Анкета": "form",
    "📬 Мои обращения": "tickets",
    "⚙️ Настройки": "settings",
}

COMMANDS = [
    ("start", "Начать, главное меню"), ("check", "Проверить документ по фото или PDF"), ("ask", "Задать вопрос по Положению"),
    ("form", "Анкета: заявление о переводе или зачислении"), ("tickets", "Мои обращения в деканат"), ("status", "Статус обращения по номеру и коду"),
    ("settings", "Уведомления и мои данные"), ("app", "Открыть приложение"), ("forget", "Удалить мои данные"), ("privacy", "Что вы храните обо мне"),
    ("help", "Что я умею"),
]


def esc(x: Any) -> str:
    return escape(str(x if x is not None else ""), quote=False)


def short(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def split_message(text: str, limit: int = LIMIT) -> list[str]:
    """Длинный текст — несколькими сообщениями, по границам строк."""
    text = text.strip()
    if len(text) <= limit:
        return [text] if text else []
    parts, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # одна строка длиннее лимита — режем по словам
            cut = line.rfind(" ", 0, limit) or limit
            cut = cut if cut > 0 else limit
            if cur:
                parts.append(cur)
                cur = ""
            parts.append(line[:cut])
            line = line[cut:].lstrip()
        if len(cur) + len(line) + 1 > limit:
            parts.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()]


# ---------------------------------------------------------------- клавиатуры
def btn(text: str, data: Optional[str] = None, url: Optional[str] = None, web_app: Optional[str] = None) -> dict:
    out: dict = {"text": text}
    if data is not None:
        out["callback_data"] = data[:64]
    if url:
        out["url"] = url
    if web_app:
        out["web_app"] = {"url": web_app}
    return out


def kb(*rows: list) -> dict:
    return {"inline_keyboard": [list(r) for r in rows if r]}


def menu_keyboard(webapp_url: Optional[str] = None) -> dict:
    """Постоянное меню под полем ввода: те же действия, что команды, — для тех, кому удобнее нажимать."""
    rows = [[{"text": "📄 Проверить документ"}, {"text": "❓ Вопрос по Положению"}], [{"text": "📝 Анкета"}, {"text": "📬 Мои обращения"}]]
    last: list = [{"text": "⚙️ Настройки"}]
    if webapp_url:
        last.append({"text": "🌐 Приложение", "web_app": {"url": webapp_url}})
    rows.append(last)
    return {"keyboard": rows, "resize_keyboard": True, "is_persistent": True}


def options_keyboard(options: list, dialog_active: bool) -> Optional[dict]:
    """Варианты ответа анкеты кнопками (callback o:<номер>); короткие — по два в ряд. В анкете добавляются «Назад» и «Отмена»."""
    rows: list[list] = []
    row: list = []
    for i, opt in enumerate(options):
        b = btn(short(str(opt), 40), f"o:{i}")
        if len(str(opt)) <= 14:
            row.append(b)
            if len(row) == 2:
                rows.append(row)
                row = []
        else:
            if row:
                rows.append(row)
                row = []
            rows.append([b])
    if row:
        rows.append(row)
    if dialog_active:
        rows.append([btn("↩ Назад", "o:back"), btn("✖ Отмена", "o:cancel")])
    return kb(*rows) if rows else None


# ---------------------------------------------------------------- ответы
def verdict_title(verdict: str, intent: str = "") -> tuple[str, str]:
    """(значок, заголовок): как на сайте — «Документ в порядке» только для проверки комплектности."""
    if verdict == "ok":
        return ("✅", "Ответ найден") if intent in ("fact", "rule", "document") else ("✅", "Документ в порядке")
    if verdict == "missing":
        return "⚠️", "Чего-то не хватает"
    return "❔", "Ответа нет"


def render_answer(ans: dict) -> str:
    """Ответ секретаря: вывод, откуда он взят (пункт Положения или поле документа), чего не хватает и оговорка о деканате."""
    icon, title = verdict_title(ans.get("verdict", "no_answer"), ans.get("intent", ""))
    report = ans.get("report") or {}
    lines = [f"{icon} <b>{esc(title)}</b>", esc(ans.get("text", ""))]
    src: list[str] = []
    for c in (report.get("clauses") or [])[:3]:
        quote = f" — «{esc(short(c.get('quote', ''), 160))}»" if c.get("quote") else ""
        src.append(f"• {esc(c.get('label') or c.get('ref'))}{quote}")
    for f in (report.get("document_fields") or [])[:4]:
        src.append(f"• {esc(f.get('label'))}: {esc(short(f.get('value', ''), 100))}")
    if src:
        lines += ["", "<b>Откуда это:</b>"] + src
    if report.get("missing"):
        lines += ["", "<b>Не хватает:</b> " + esc(", ".join(report["missing"][:8]))]
    if report.get("doubtful"):
        lines += ["<b>Прочитано неуверенно:</b> " + esc(", ".join(report["doubtful"][:8]))]
    for n in (ans.get("notes") or [])[:2]:
        lines.append(f"<i>{esc(short(n, 220))}</i>")
    lines += ["", f"<i>{esc(report.get('disclaimer') or 'Это предварительная оценка, а не решение. Окончательное решение принимает сотрудник деканата.')}</i>"]
    return "\n".join(lines)


def render_card(fields: list[dict], limit: int = 14) -> str:
    """Карточка полей документа (как на сайте): одна строка на поле. Составное поле («номер и дата») — одним значением, подпись и печать — «есть / нет»,
    сомнительное значение — с пометкой ⚠️, ненайденное — «не найдено»."""
    lines = []
    for f in fields[:limit]:
        title = esc(f.get("title"))
        parts = f.get("parts") or []
        if parts and f.get("kind") == "marks":
            found = [bool(p.get("found")) for p in parts]
            icon = "✓" if all(found) else ("◐" if any(found) else "✗")
            lines.append(f"{icon} {title}: " + " · ".join(f"{esc(str(p.get('title', '')).lower())} — {'есть' if p.get('found') else 'нет'}" for p in parts))
        elif parts:
            found = [bool(p.get("found")) for p in parts]
            have = " · ".join(esc(short(p.get("value", ""), 60)) for p in parts if p.get("found"))
            if not any(found):
                lines.append(f"✗ {title}: не найдено")
            else:
                value = esc(short(f.get("value", ""), 90)) if f.get("found") and f.get("value") not in (None, "", "не найдено") else have
                missing = [str(p.get("title", "")) for p in parts if not p.get("found")]
                icon = "⚠️" if f.get("doubt") else ("✓" if all(found) else "◐")
                lines.append(f"{icon} {title}: {value}" + (f" <i>(не найдено: {esc(', '.join(missing))})</i>" if missing else ""))
        elif f.get("found"):
            lines.append(f"{'⚠️' if f.get('doubt') else '✓'} {title}: {esc(short(f.get('value', ''), 90))}")
        else:
            lines.append(f"✗ {title}: не найдено")
    return "\n".join(lines)


def progress_bar(fraction: float, width: int = 10) -> str:
    full = max(0, min(width, int(round(fraction * width))))
    return "▰" * full + "▱" * (width - full)


STAGE_TITLES_RU = {"load": "открываю и выравниваю снимок", "ocr": "читаю текст", "classify": "определяю тип документа", "extract": "заполняю карточку",
                   "answer": "ищу основание в Положении"}


def render_progress(stage: str, fraction: float) -> str:
    """Сообщение «Читаю документ…» с этапом и шкалой — чтобы ожидание не выглядело зависанием."""
    f = max(0.0, min(1.0, fraction))
    return f"⏳ Читаю документ…\n{progress_bar(f)} {int(round(f * 100))}% · {STAGE_TITLES_RU.get(stage, 'работаю')}"


def render_document(record: Optional[dict], fields: list[dict], warnings: Optional[list] = None) -> str:
    """Шапка проверки документа: тип и карточка полей (поля — в виде FieldResult.to_dict())."""
    if not record:
        return ""
    dt = record.get("doc_type") or {}
    if dt.get("id"):
        how = {"auto": "определён автоматически", "manual": "тип выбран вручную", "generic": "другой документ"}.get(dt.get("how"), "")
        head = f"📄 <b>{esc(dt.get('title'))}</b>" + (f" <i>({how})</i>" if how else "")
    else:
        head = "📄 <b>Документ</b> <i>(тип не определён — отвечаю по тексту документа)</i>"
    parts = [head]
    if fields:
        parts += ["", render_card(fields)]
    for w in (warnings or [])[:2]:
        parts.append(f"<i>{esc(short(w, 220))}</i>")
    return "\n".join(parts)


# ---------------------------------------------------------------- обращения
def fmt_date(iso: Optional[str]) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except ValueError:
        return str(iso)[:10]


def ticket_line(t: dict) -> str:
    return f"{STATUS_EMOJI.get(t['status'], '•')} № {t['id']} · {esc(t.get('topic') or 'Обращение')} · {fmt_date(t.get('created_at'))} — {esc(STATUS_TITLES.get(t['status'], t['status']))}"


def render_ticket(t: dict) -> str:
    lines = [f"{STATUS_EMOJI.get(t['status'], '•')} <b>Обращение № {t['id']}</b>", esc(t.get("topic") or ""),
             f"Статус: <b>{esc(STATUS_TITLES.get(t['status'], t['status']))}</b>", f"Отправлено: {fmt_date(t.get('created_at'))}"]
    if t.get("updated_at") and t.get("updated_at") != t.get("created_at"):
        lines.append(f"Обновлено: {fmt_date(t.get('updated_at'))}")
    if t.get("reply"):
        lines += ["", "<b>Сообщение от деканата:</b>", esc(t["reply"])]
    elif t["status"] in ("new", "in_progress"):
        lines += ["", "Ответа пока нет. Как только сотрудник ответит или изменит статус, я пришлю уведомление."]
    return "\n".join(x for x in lines if x is not None)


def render_tickets(items: list[dict]) -> str:
    if not items:
        return ("У вас пока нет обращений в деканат.\n\nОтправить обращение можно после анкеты: команда /form. "
                "Если вы отправляли обращение на сайте без входа, привяжите его: /status <i>номер код</i>.")
    return "<b>Ваши обращения</b>\n\n" + "\n".join(ticket_line(t) for t in items[:10]) + "\n\nНажмите на номер, чтобы открыть."


def render_next_steps(ns: dict) -> tuple[str, str]:
    """(текст, текст напоминания). Если в Положении об этом ничего нет — так и сказано, без выдумок."""
    docs, term = ns.get("documents"), ns.get("deadline")
    lines = ["📋 <b>Что дальше</b>"]
    remind = []
    if docs:
        refs = (" (" + ", ".join(esc(x) for x in docs.get("sources", [])) + ")") if docs.get("sources") else ""
        lines += ["", f"<b>Что принести:</b> {esc(docs['text'])}{refs}"]
        remind.append("Что принести: " + short(docs["text"], 300))
    if term:
        kind = "рабочих" if term.get("working") else "календарных"
        lines += ["", f"<b>Срок решения:</b> до {fmt_date(term['date'])} ({term['days']} {kind} дн.) — {esc(term.get('source'))}"]
        remind.append(f"Срок решения: до {fmt_date(term['date'])}")
    if not docs and not term:
        lines += ["", "В Положении об этом ничего нет — уточните в деканате."]
    lines += ["", "<i>Окончательное решение принимает сотрудник деканата.</i>"]
    return "\n".join(lines), "\n".join(remind)


def render_submission(prev: dict, contact: str = "") -> str:
    p = prev.get("payload") or {}
    lines = ["📤 <b>Что уйдёт в деканат</b>", f"Запрос: {esc(p.get('request'))}"]
    lines += [f"• {esc(k)}: {esc(v)}" for k, v in (p.get("fields") or {}).items()]
    if contact:
        lines.append(f"• Контакт: {esc(contact)}")
    if prev.get("excluded"):
        lines.append("<i>Не отправляется: " + esc(", ".join(prev["excluded"])) + ".</i>")
    if prev.get("attachment"):
        lines.append("К обращению приложится готовое заявление (DOCX).")
    lines += ["", "Отправить в очередь деканата?"]
    return "\n".join(lines)
