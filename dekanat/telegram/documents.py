"""Документ и вопросы в чате: проверка присланного файла, подсветка на снимке, ответ на вопрос по документу или Положению.

Работает как часть `TelegramBot`: поля (`api`, `storage`, `core`, `accounts` …) и общие методы (`_send`, `_edit_or_send`, `_typing`, `_text`) приходят из него.
"""

from __future__ import annotations

import hashlib
import logging
import time
from typing import Optional

import cv2

from ..assistant import OCR_DOWN_TEXT, remember_document
from ..highlight import answer_items, draw_overlay, overlay_items, shift_box
from ..ocr.engine import OcrUnavailable
from ..ocr.preprocess import UnsupportedFile
from ..qa.docqa import wants_to_see
from ..storage.topics import classify_topic
from .api import TelegramError
from .views import btn, esc, kb, render_answer, render_document, render_progress

log = logging.getLogger("dekanat.telegram")

MAX_TG_FILE = 20 * 1024 * 1024
FILE_EXT = (".jpg", ".jpeg", ".png", ".pdf")

# Быстрые вопросы по проверенному документу: одно нажатие вместо набора текста (callback q:<номер>)
QUICK_QUESTIONS = [("Чего не хватает?", "Чего не хватает в документе?"), ("Хватит для перевода?", "Хватит ли этого документа для перевода?")]


class DocumentsMixin:
    def _file(self, u: dict, file_id: str, name: str, size: int, caption: str) -> None:
        if size and size > self.max_bytes:
            self._send(u, f"Файл больше {self.max_bytes // (1024 * 1024)} МБ — Telegram не отдаёт такие файлы боту. Сожмите фото или пришлите первую страницу.")
            return
        if not self.file_limiter.hit(u["tg_id"]):
            self._send(u, "Вы прислали много документов за час. Подождите немного или воспользуйтесь сайтом.")
            return
        wait = self._send(u, render_progress("load", 0.02))
        self._typing(u, "upload_photo")
        sid = self.accounts.ensure_data_session(u)
        try:
            data = self._file_bytes(file_id)
            with self.ocr_slots:
                res = self.svc.analyze(data, name, caption.strip(), "auto", progress=self._progress_reporter(u, wait), document_first=True)
        except UnsupportedFile as exc:
            return self._edit_or_send(u, wait, esc(str(exc)))
        except OcrUnavailable as exc:
            log.error("Распознавание текста недоступно: %s", exc)
            return self._edit_or_send(u, wait, OCR_DOWN_TEXT)
        except TelegramError as exc:
            log.warning("Файл не получен: %s", exc)
            return self._edit_or_send(u, wait, "Не удалось получить файл от Telegram. Пришлите его ещё раз.")
        except Exception:
            log.exception("Ошибка при проверке документа из Telegram")
            return self._edit_or_send(u, wait, "Не удалось проверить документ. Попробуйте другой снимок — ровнее и при хорошем свете.")
        remember_document(self.storage, self.locks, sid, res, name, hashlib.sha256(data).hexdigest())
        self.storage.events.log("document_check", classify_topic(caption), res.answer.verdict, sid)
        state = self.storage.users.bot_state(u["id"])
        state["last_file"] = {"id": file_id, "name": name}
        state["last_question"] = caption.strip()
        remind = ""
        if res.answer.verdict == "missing":
            report = res.answer.report()
            remind = "Документ не подходит: не хватает — " + ", ".join(report.get("missing") or []) if report.get("missing") else "Проверьте документ: в нём не хватает данных."
        state["reminder"] = {"text": remind} if remind else {}
        self.storage.users.save_bot_state(u["id"], state)
        record = self.core.doc_record(sid)
        text = render_document(record, [f.to_dict() for f in res.fields], res.warnings) + "\n\n" + render_answer(res.answer.to_dict())
        show = bool(caption.strip() and wants_to_see(caption))  # подпись «покажи …» к фото: подсветка идёт сразу, кнопка не нужна
        actions = [btn("📝 Составить заявление", "form:any")] if show else [btn("🖼 Показать на снимке", "doc:img"), btn("📝 Составить заявление", "form:any")]
        rows = [self._quick_row(caption.strip()), actions]
        second = [btn("🗑 Забыть документ", "doc:forget")]
        if remind:
            second.insert(0, btn("⏰ Напомнить через 3 дня", "rem:3"))
        rows.append(second)
        self._edit_or_send(u, wait, text, kb(*rows))
        if show:
            self._send_overlay(u, res, auto=True)

    def _progress_reporter(self, u: dict, wait: Optional[dict]):
        """Обновляет сообщение «Читаю документ…» по ходу распознавания (не чаще раза в 1,5 с и только при смене этапа или заметном сдвиге)."""
        last = {"t": 0.0, "stage": "load", "frac": 0.02}

        def report(stage: str, _label: str, fraction: float) -> None:
            now = time.time()
            if not wait or now - last["t"] < 1.5 or (stage == last["stage"] and fraction - last["frac"] < 0.12):
                return
            last.update(t=now, stage=stage, frac=fraction)
            try:
                self.api.edit_message_text(u["tg_id"], wait["message_id"], render_progress(stage, fraction))
            except Exception:  # индикатор — удобство, а не условие: сбой правки не должен ронять проверку
                pass

        return report

    @staticmethod
    def _quick_row(asked: str = "") -> list:
        """Кнопки быстрых вопросов (кроме того, что только что задан)."""
        return [btn(label, f"q:{i}") for i, (label, text) in enumerate(QUICK_QUESTIONS) if text != asked]

    def _file_bytes(self, file_id: str) -> bytes:
        if file_id in self._files:
            self._files.move_to_end(file_id)
            return self._files[file_id]
        data = self.api.download_file(file_id, self.max_bytes)
        self._files[file_id] = data
        while len(self._files) > 3 or sum(len(v) for v in self._files.values()) > 40 * 1024 * 1024:
            self._files.popitem(last=False)
        return data

    def _drop_file(self, u: dict) -> None:
        """Забытый документ не должен оставаться и в памяти процесса."""
        last = self.storage.users.bot_state(u["id"]).get("last_file")
        if last:
            self._files.pop(last.get("id"), None)

    @staticmethod
    def _zoom(img, boxes, items=None) -> Optional[bytes]:
        """Крупный план найденного места: у маленького слова на целой странице подсветка в чате почти не видна. None — место и так крупное.
        Вырезается из чистой страницы, а рамки (items) рисуются уже на увеличенном фрагменте: подписи остаются мелкими и не закрывают текст."""
        h, w = img.shape[:2]
        x0, y0 = min(b.x0 for b in boxes), min(b.y0 for b in boxes)
        x1, y1 = max(b.x1 for b in boxes), max(b.y1 for b in boxes)
        if (x1 - x0) * (y1 - y0) > 0.10 * w * h:
            return None
        # окно примерно 4:3 вокруг места: по бокам — соседние слова, сверху и снизу — соседние строки (по одному слову не понять, где оно)
        cw = min(float(w), (x1 - x0) + 2 * max(120.0, 0.35 * (x1 - x0)))
        ch = min(float(h), max(cw * 0.75, (y1 - y0) + 120.0))
        cx0 = int(min(max((x0 + x1) / 2 - cw / 2, 0), w - cw))
        cy0 = int(min(max((y0 + y1) / 2 - ch / 2, 0), h - ch))
        cx1, cy1 = int(cx0 + cw), int(cy0 + ch)
        crop = img[cy0:cy1, cx0:cx1]
        if crop.size == 0:
            return None
        k = 1.0
        if crop.shape[1] < 1000:
            k = min(4.0, 1000 / crop.shape[1])
            crop = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        if items:
            crop = draw_overlay(crop, [(shift_box(b, cx0, cy0, k), label, color) for b, label, color in items], 0, boost=1.7)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 88])
        return buf.tobytes() if ok else None

    def _show_on_photo(self, u: dict, auto: bool = False) -> None:
        """Снимок с подсветкой найденного (и крупный план, если место маленькое). auto — вызвано просьбой «покажи …»: молча ничего не делает, если показывать нечего."""
        state = self.storage.users.bot_state(u["id"])
        last = state.get("last_file")
        if not last:
            if not auto:
                self._send(u, "Сначала пришлите документ — тогда я смогу показать найденное на снимке.")
            return
        sid = self.accounts.ensure_data_session(u)
        record = self.core.doc_record(sid)
        self._typing(u, "upload_photo")
        try:
            data = self._file_bytes(last["id"])
            with self.ocr_slots:
                res = self.svc.analyze(data, last["name"], state.get("last_question", ""), (record or {}).get("doc_type", {}).get("id") or "auto", document_first=True)
        except (UnsupportedFile, OcrUnavailable, TelegramError):
            if not auto:
                self._send(u, "Не удалось показать подсветку: файл больше недоступен. Пришлите документ ещё раз.")
            return
        self._send_overlay(u, res, auto)

    def _send_overlay(self, u: dict, res, auto: bool = False) -> None:
        """Отправляет снимок(и) с подсветкой по готовому результату проверки (и крупный план, если место маленькое).
        Если ответ опирается на конкретные места («покажи печать»), подсвечиваются только они; иначе — все найденные поля."""
        focus = answer_items(res.answer) if res.answer is not None else []
        items = focus or overlay_items(res.fields, res.answer)
        pages = sorted({box.page for box, _, _ in items if 0 <= box.page < len(res.original_pages)})[:3]
        if not pages:
            if not auto:
                self._send(u, "На снимке нечего подсвечивать: найденных полей нет. Попробуйте задать вопрос, например «Покажи ФИО».")
            return
        for p in pages:
            page_items = [it for it in items if it[0].page == p]
            drawn = draw_overlay(res.original_pages[p], page_items, p)
            ok, buf = cv2.imencode(".jpg", drawn, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                found = sum(len(s.boxes) for s in res.answer.sources)
                if found > 1:
                    caption = f"Найдено мест: {found} — они пронумерованы."
                elif focus:
                    caption = "Найденное подсвечено оранжевой рамкой."
                else:
                    caption = "Найденное подсвечено: зелёным — поля карточки, оранжевым — источник ответа."
                self.api.send_photo(u["tg_id"], buf.tobytes(), caption, "highlight.jpg")
            asked_items = [it for it in focus if it[0].page == p] or page_items  # крупно — то, о чём спросили
            asked = [b for b, _, _ in asked_items]
            zoom = self._zoom(res.original_pages[p], asked, asked_items) if 0 < len(asked) <= 3 else None  # для одного-двух мест; разбросанные вхождения в один кадр не собрать
            if zoom:
                self.api.send_photo(u["tg_id"], zoom, "Крупно", "zoom.jpg")

    # ------------------------------------------------------------------ вопросы вне анкеты
    def _question(self, u: dict, sid: str, text: str) -> None:
        record = self.core.doc_record(sid)
        state = self.storage.users.bot_state(u["id"])
        last = state.get("last_file")
        try:
            if record and record["doc_type"]["id"]:
                ans = self.core.answer_question(sid, text, record)
            elif record and last:  # тип документа не определён: ответ по тексту документа — читаем файл заново (распознавание кешируется)
                data = self._file_bytes(last["id"])
                with self.ocr_slots:
                    ans = self.svc.analyze(data, last["name"], text, "auto", document_first=True).answer.to_dict()
                self.storage.events.log("question", classify_topic(text), ans["verdict"], sid)
            else:
                ans = self.core.answer_question(sid, text, None)
        except (UnsupportedFile, OcrUnavailable, TelegramError):
            ans = self.core.answer_question(sid, text, None)
        state["last_question"] = text
        self.storage.users.save_bot_state(u["id"], state)
        show = bool(record and last and wants_to_see(text))  # «покажи …», «найди …», «где …»: снимок идёт сразу, кнопка не нужна
        markup = None
        if record and last:
            markup = kb(self._quick_row(text), [] if show else [btn("🖼 Показать на снимке", "doc:img")])
        self._send(u, render_answer(ans), markup)
        if show:
            self._show_on_photo(u, auto=True)
