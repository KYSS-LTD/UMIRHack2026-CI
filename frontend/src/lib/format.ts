import type { Box, FieldResult, Verdict } from "../types";

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} Б`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(n < 10 * 1024 ? 1 : 0)} КБ`;
  return `${(n / 1024 / 1024).toFixed(1)} МБ`;
}

export const pct = (x: number): number => Math.round(Math.max(0, Math.min(1, x || 0)) * 100);

export function formatSeconds(s: number): string {
  return s < 10 ? `${s.toFixed(1)} с` : `${Math.round(s)} с`;
}

/** «5 минут назад», «вчера в 14:03» — для истории проверок. */
export function relativeTime(ts: number, now = Date.now()): string {
  const diff = Math.max(0, now - ts);
  const min = Math.floor(diff / 60000);
  if (min < 1) return "только что";
  if (min < 60) return `${min} мин назад`;
  const d = new Date(ts);
  const hm = d.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
  const hours = Math.floor(min / 60);
  if (hours < 24 && new Date(now).getDate() === d.getDate()) return `сегодня в ${hm}`;
  return `${d.toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} в ${hm}`;
}

export const VERDICT_META: Record<Verdict, { title: string; icon: string; tone: "ok" | "warn" | "none" }> = {
  ok: { title: "Документ в порядке", icon: "✓", tone: "ok" },
  missing: { title: "Чего-то не хватает", icon: "!", tone: "warn" },
  no_answer: { title: "Ответа нет", icon: "?", tone: "none" },
};

const STAMP_TEXT: Record<Verdict, string> = { ok: "В порядке", missing: "Не хватает", no_answer: "Ответа нет" };
/** Вопросы, на которые отвечают значением или пунктом, а не проверкой комплектности документа. */
const ANSWER_INTENTS = new Set(["fact", "rule", "document"]);

/**
 * Заголовок и надпись штампа зависят от вопроса: «Документ в порядке» говорят только о проверке комплектности.
 * На вопрос о правилах, о значении поля или о тексте документа ответ просто найден — даже если в документе чего-то не хватает.
 */
export function verdictMeta(verdict: Verdict, intent?: string): { title: string; icon: string; tone: "ok" | "warn" | "none"; stamp: string } {
  const base = VERDICT_META[verdict] ?? VERDICT_META.no_answer;
  if (verdict === "ok" && intent && ANSWER_INTENTS.has(intent)) return { ...base, title: "Ответ найден", stamp: "Найдено" };
  return { ...base, stamp: STAMP_TEXT[verdict] ?? STAMP_TEXT.no_answer };
}

/** Объединяет рамки в одну — чтобы приблизить снимок к нужному фрагменту. */
export function unionBoxes(boxes: Box[]): Box | null {
  if (!boxes.length) return null;
  return boxes.reduce((u, b) => ({ page: u.page, x0: Math.min(u.x0, b.x0), y0: Math.min(u.y0, b.y0), x1: Math.max(u.x1, b.x1), y1: Math.max(u.y1, b.y1) }), { ...boxes[0]! });
}

export function fieldBoxes(f: FieldResult): Box[] {
  return [...f.boxes, ...f.parts.flatMap((p) => p.boxes)];
}

/** Первая страница, на которой есть рамки (для автоматического переключения страницы просмотрщика). */
export function firstPageWithBoxes(boxes: Box[]): number {
  return boxes.length ? boxes[0]!.page : 0;
}

/** Склонение по числу: plural(25, ["пункт", "пункта", "пунктов"]) -> «пунктов». */
export function plural(n: number, forms: [string, string, string]): string {
  const a = Math.abs(n) % 100;
  const b = a % 10;
  if (a > 10 && a < 20) return forms[2];
  if (b > 1 && b < 5) return forms[1];
  if (b === 1) return forms[0];
  return forms[2];
}
