import type { SampleItem } from "../types";

/** Пример, который лучше всего показывает ценность сервиса: справка, где «чего не хватает». Нет подходящего — первый из списка. */
export const pickDemo = (samples: SampleItem[]): SampleItem | null => samples.find((s) => /нет|не хватает/i.test(s.title)) ?? samples[0] ?? null;
