import { AUTO_TYPE, type DocTypeSummary, GENERIC_TYPE, type Suggestion } from "../types";

const GENERIC_SUGGESTIONS: Suggestion[] = [
  { kind: "field", text: "О чём документ?" },
  { kind: "field", text: "Какая дата документа?" },
  { kind: "field", text: "Что указано в п. 1?" },
];

const AUTO_SUGGESTIONS: Suggestion[] = [
  { kind: "check", text: "Достаточно ли этого документа?" },
  { kind: "check", text: "Чего не хватает в документе?" },
  { kind: "rules", text: "Сколько дней рассматривается заявление?" },
];

/** Анкета: после проверенной справки её поля подставляются в ответы. */
const FORM_SUGGESTION: Suggestion = { kind: "form", text: "Я хочу перевестись" };

const lcFirst = (s: string) => (!s || /^[А-ЯЁA-Z]{2}/.test(s) ? s : s[0]!.toLowerCase() + s.slice(1));

/**
 * С документом на столе секретарь отвечает по документу, а по Положению — только если оно упомянуто.
 * Поэтому подсказки о правилах при загруженном документе явно называют Положение.
 */
export function aboutRegulation(list: Suggestion[], documentLoaded: boolean): Suggestion[] {
  if (!documentLoaded) return list;
  return list.map((s) => (s.kind === "rules" && !/положени/i.test(s.text) ? { ...s, text: `По Положению: ${lcFirst(s.text)}` } : s));
}

/** Подсказки вопросов для выбранного (или уже определённого) типа: проверка, значение поля, правила; withForm — и анкета. */
export function suggestionsFor(types: DocTypeSummary[], docType: string, detectedId: string | null | undefined, rules: Suggestion[], withForm = false): Suggestion[] {
  const form = withForm ? [FORM_SUGGESTION] : [];
  if (docType === GENERIC_TYPE) return [...GENERIC_SUGGESTIONS, ...form];
  const id = docType !== AUTO_TYPE ? docType : detectedId;
  const t = types.find((x) => x.id === id);
  if (t?.suggestions?.length) return [...t.suggestions, ...form];
  return [...AUTO_SUGGESTIONS.slice(0, 2), ...(rules.length ? rules.slice(0, 2) : AUTO_SUGGESTIONS.slice(2)), ...form];
}
