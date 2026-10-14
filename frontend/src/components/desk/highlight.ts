import type { Box } from "../../types";

/** То, что подсвечено на снимке: источник ответа, поле карточки или фрагмент под курсором. */
export interface Highlight {
  id: string;
  label: string;
  boxes: Box[];
}

export interface CropGeometry {
  /** размеры вырезки в пикселях исходного снимка */
  w: number;
  h: number;
  /** left/top вырезки */
  x: number;
  y: number;
  /** background-position в процентах */
  px: number;
  py: number;
}

/** Прямоугольник вокруг рамок с полями: показывает «фрагмент фото, где найдено поле». */
export function cropAround(boxes: Box[], iw: number, ih: number): CropGeometry | null {
  if (!boxes.length || iw <= 0 || ih <= 0) return null;
  const x0 = Math.min(...boxes.map((b) => b.x0));
  const y0 = Math.min(...boxes.map((b) => b.y0));
  const x1 = Math.max(...boxes.map((b) => b.x1));
  const y1 = Math.max(...boxes.map((b) => b.y1));
  const bh = Math.max(1, y1 - y0);
  const padX = 36;
  const padY = Math.max(bh * 0.5, 44);
  let x = Math.max(0, x0 - padX);
  let y = Math.max(0, y0 - padY);
  let w = Math.min(iw - x, x1 - x0 + 2 * padX);
  let h = Math.min(ih - y, bh + 2 * padY);
  // слишком узкая вырезка плохо читается: фрагмент делаем шире высоты (печать — крупное поле, высота у неё немалая)
  const minW = Math.min(iw, Math.max(380, h * 1.6));
  if (w < minW) {
    x = Math.max(0, Math.min(iw - minW, x - (minW - w) / 2));
    w = minW;
  }
  // снимок узкий, а фрагмент всё равно выше, чем шире, — подрезаем по высоте вокруг поля
  const maxH = w / 1.2;
  if (h > maxH) {
    const nh = Math.max(bh + 24, maxH);
    y = Math.max(0, Math.min(ih - nh, (y0 + y1) / 2 - nh / 2));
    h = nh;
  }
  const px = iw - w < 1 ? 0 : (x / (iw - w)) * 100;
  const py = ih - h < 1 ? 0 : (y / (ih - h)) * 100;
  return { w, h, x, y, px, py };
}

/** Документ, лежащий на столе: все, что показывали в этой сессии, можно переключать одним щелчком. */
export interface DeskDoc {
  key: string;
  file: Blob;
  name: string;
}
