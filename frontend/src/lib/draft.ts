/** Недописанный вопрос на столе: переживает случайную перезагрузку и переход между разделами. Только в этой вкладке (sessionStorage), на диск не пишется. */
export const DRAFT_KEY = "dekanat.draft";
const MAX = 2000;

export function readDraft(storage: Pick<Storage, "getItem"> = sessionStorage): string {
  try {
    return storage.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

export function saveDraft(text: string, storage: Pick<Storage, "setItem" | "removeItem"> = sessionStorage): void {
  try {
    if (text.trim()) storage.setItem(DRAFT_KEY, text.slice(0, MAX));
    else storage.removeItem(DRAFT_KEY);
  } catch {
    /* хранилище недоступно — черновик просто не сохраняется */
  }
}
