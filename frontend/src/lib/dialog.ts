import type { AnalysisResult, DialogState, FieldCorrection, SubmitResult } from "../types";

/** Что уходит на сервер при нажатии быстрого ответа: подпись кнопки и команда анкеты могут различаться. */
const OPTION_TEXT: Record<string, string> = { "Да, начать": "да", Нет: "нет", Верно: "верно", Исправить: "исправить", "Сформировать документ": "сформировать", "Прекратить анкету": "отмена" };

/** Быстрый ответ секретаря после отправки обращения: переход на страницу статуса (серверу не уходит). */
export const OPEN_STATUS = "Открыть страницу статуса";

/** Что секретарь пишет сразу после отправки обращения: номер, код, что с письмом и предложение открыть страницу статуса. */
export function statusOfferText(r: SubmitResult): string {
  const mail = r.mail;
  const letter =
    !mail || mail.status === "none"
      ? ""
      : mail.status === "sent"
        ? `Письмо с номером и кодом отправлено на ${mail.to}.`
        : mail.status === "failed"
          ? `Письмо на ${mail.to} отправить не удалось — сохраните номер и код из этого сообщения.`
          : mail.status === "disabled"
            ? "Почта на сервере не настроена, поэтому письмо не отправляется — сохраните номер и код."
            : "Письма уходят только на почту — сохраните номер и код.";
  return [`Обращение № ${r.ticket_id} отправлено в деканат.`, r.code ? `Код для проверки статуса: ${r.code} — сохраните его.` : "", letter, "Статус и ответ деканата будут на странице «Статус обращения». Открыть её?"]
    .filter(Boolean)
    .join(" ");
}

export const optionText = (label: string): string => OPTION_TEXT[label] ?? label;

/** Анкета ещё не закончена: ответы в поле ввода идут ей, а не в проверку документа. */
export const isQuizState = (s: DialogState | null | undefined): boolean => s === "screening" || s === "review" || s === "document_generation";

export interface QuestionText {
  intro: string;
  label: string;
  body: string;
}

/** «Вступление\n\nВопрос 2 из 6. Текст вопроса» → вступление, метка «Вопрос 2 из 6» и сам вопрос. */
export function splitQuestion(text: string): QuestionText {
  const m = /(^|\n\n)Вопрос (\d+) из (\d+)\. /.exec(text);
  if (!m) return { intro: "", label: "", body: text };
  return { intro: text.slice(0, m.index).trim(), label: `Вопрос ${m[2]} из ${m[3]}`, body: text.slice(m.index + m[0].length) };
}

export interface ReviewSheet {
  /** строки до списка (например, «Продолжаем с того же места.») */
  head: string[];
  items: { title: string; value: string; skipped: boolean }[];
  date: string;
  fixed: string;
  notes: string[];
  tail: string[];
}

/** Обзор анкеты от сервера («1. ФИО: …», «Дата заявления: …», «— замечание») → строки листа сверки. */
export function parseReview(text: string): ReviewSheet | null {
  const sheet: ReviewSheet = { head: [], items: [], date: "", fixed: "", notes: [], tail: [] };
  for (const line of text.split("\n")) {
    let m = /^(\d+)\. ([^:]+): (.*)$/.exec(line);
    if (m) {
      sheet.items.push({ title: m[2]!, value: m[3]!, skipped: m[3]!.startsWith("—") });
      continue;
    }
    m = /^Дата заявления: (.*)$/.exec(line);
    if (m) {
      sheet.date = m[1]!;
      continue;
    }
    if (/^Исправлено:/.test(line)) sheet.fixed = line;
    else if (line.startsWith("— ")) sheet.notes.push(line.slice(2));
    else if (line && !/^Проверьте собранные данные/.test(line) && !/^Обратите внимание/.test(line)) (sheet.items.length ? sheet.tail : sheet.head).push(line);
  }
  return sheet.items.length ? sheet : null;
}

/** Повторная проверка того же файла пришла с исправлениями пользователя: помечаем исправленные поля и части. */
export function markCorrected(result: AnalysisResult): AnalysisResult {
  const units = new Set(result.corrected ?? []);
  if (!units.size) return result;
  const fields = result.fields.map((f) => {
    const parts = f.parts.map((p) => (units.has(`${f.id}.${p.id}`) ? { ...p, corrected: true } : p));
    return { ...f, parts, corrected: units.has(f.id) || parts.some((p) => p.corrected) };
  });
  return { ...result, fields };
}

/**
 * Правка поля на столе: сервер вернул карточку без рамок и ответ, пересчитанный по ней.
 * Значения берём новые, рамки на снимке — прежние; исправленные пользователем поля и части помечаются.
 */
export function mergeCorrection(result: AnalysisResult, res: FieldCorrection): AnalysisResult {
  const corrected = new Set(res.document.corrected);
  const fields = result.fields.map((old) => {
    const fresh = res.fields.find((f) => f.id === old.id);
    if (!fresh) return old;
    const parts = old.parts.map((op) => {
      const np = fresh.parts.find((p) => p.id === op.id);
      return np ? { ...op, ...np, boxes: op.boxes, corrected: corrected.has(`${old.id}.${op.id}`) } : op;
    });
    return { ...old, ...fresh, boxes: old.boxes, parts, corrected: corrected.has(old.id) || parts.some((p) => p.corrected) };
  });
  return { ...result, fields, answer: res.answer ?? result.answer };
}
