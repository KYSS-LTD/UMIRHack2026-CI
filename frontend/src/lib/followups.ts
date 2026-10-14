import type { AnalysisResult } from "../types";

const norm = (s: string) => s.toLowerCase().replace(/ё/g, "е").replace(/\s+/g, " ").trim();
// «Форма обучения» -> «форма обучения», но аббревиатуру «ФИО студента» не портим
const lcFirst = (s: string) => (!s || /^[А-ЯЁA-Z]{2}/.test(s) ? s : s[0]!.toLowerCase() + s.slice(1));

/**
 * «Спросить дальше»: вопросы, которые сервис точно умеет разобрать, исходя из результата.
 * Уже заданный вопрос не повторяется; для чужого документа предлагаются вопросы по его тексту.
 */
export function followUps(result: AnalysisResult, asked: string, rules: string[] = [], limit = 4): string[] {
  const out: string[] = [];
  const push = (q: string) => {
    if (norm(q) !== norm(asked) && !out.some((x) => norm(x) === norm(q))) out.push(q);
  };
  // в документе ответа нет, а в Положении есть: первым предлагаем тот же вопрос «по Положению»
  const regulation = result.answer.debug?.regulation_question;
  if (typeof regulation === "string" && regulation) push(regulation);

  if (!result.doc_type.id) {
    ["О чём документ?", "Какая дата документа?", "Какой номер документа?", "Что указано в п. 1?"].forEach(push);
    return out.slice(0, limit);
  }

  if (result.answer.verdict === "missing") push("Чего не хватает в документе?");
  else push("Хватит ли этого документа?");
  result.fields
    .filter((f) => f.status === "found" && f.kind !== "marks" && f.kind !== "composite")
    .slice(0, 2)
    .forEach((f) => push(`Укажи ${lcFirst(f.title)}`));
  rules.slice(0, 2).forEach(push);
  return out.slice(0, limit);
}

/** Текст ответа для буфера обмена: вердикт, ответ и источники. */
export function answerToText(title: string, text: string, sources: string[]): string {
  return [`${title}: ${text}`, sources.length ? `Источник: ${sources.join(", ")}` : ""].filter(Boolean).join("\n");
}
