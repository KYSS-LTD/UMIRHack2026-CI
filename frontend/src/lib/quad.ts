import type { Box } from "../types";

export type Pt = [number, number];

/** Наклонная рамка снимка, сделанного под углом: четыре угла строки. Битые данные считаются отсутствующими — тогда рисуется обычный прямоугольник. */
export function quadOf(b: Box): Pt[] | null {
  const q = b.quad;
  if (!Array.isArray(q) || q.length !== 4) return null;
  return q.every((p) => Array.isArray(p) && p.length === 2 && Number.isFinite(p[0]) && Number.isFinite(p[1])) ? q : null;
}

/** Значение атрибута points для svg-многоугольника. */
export function pointsAttr(q: Pt[]): string {
  return q.map(([x, y]) => `${Math.round(x * 10) / 10},${Math.round(y * 10) / 10}`).join(" ");
}
