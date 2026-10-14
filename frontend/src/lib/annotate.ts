import type { Box } from "../types";
import { quadOf } from "./quad";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
  /** наклонная рамка (снимок под углом), уже в масштабе картинки */
  quad?: [number, number][];
}

/** Рамки приходят в пикселях исходного снимка, а картинка может быть уменьшенной копией: пересчитываем. */
export function scaleBoxes(boxes: Box[], page: number, fromW: number, toW: number, fromH: number, toH: number): Rect[] {
  const kx = toW / Math.max(1, fromW);
  const ky = toH / Math.max(1, fromH);
  return boxes
    .filter((b) => b.page === page)
    .map((b): Rect => {
      const rect: Rect = { x: b.x0 * kx, y: b.y0 * ky, w: (b.x1 - b.x0) * kx, h: (b.y1 - b.y0) * ky };
      const quad = quadOf(b);
      if (quad) rect.quad = quad.map(([x, y]) => [x * kx, y * ky]);
      return rect;
    });
}

/** Рисует на копии снимка рамки найденных полей: «снимок с подсветкой» для отчёта или переписки. */
export function renderAnnotated(img: HTMLImageElement, origW: number, origH: number, page: number, others: Box[], hits: Box[]): HTMLCanvasElement {
  const canvas = document.createElement("canvas");
  canvas.width = img.naturalWidth;
  canvas.height = img.naturalHeight;
  const ctx = canvas.getContext("2d")!;
  ctx.drawImage(img, 0, 0);
  const unit = Math.max(1, canvas.width / 900);
  const draw = (rects: Rect[], stroke: string, fill: string, width: number) => {
    ctx.lineWidth = width * unit;
    ctx.strokeStyle = stroke;
    ctx.fillStyle = fill;
    ctx.lineJoin = "round";
    for (const r of rects) {
      if (r.quad) {
        ctx.beginPath();
        r.quad.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
        ctx.closePath();
        ctx.fill();
        ctx.stroke();
      } else {
        ctx.fillRect(r.x, r.y, r.w, r.h);
        ctx.strokeRect(r.x, r.y, r.w, r.h);
      }
    }
  };
  draw(scaleBoxes(others, page, origW, canvas.width, origH, canvas.height), "rgba(60,120,210,.85)", "rgba(60,120,210,.10)", 1.5);
  draw(scaleBoxes(hits, page, origW, canvas.width, origH, canvas.height), "#e08a0b", "rgba(224,138,11,.22)", 3);
  return canvas;
}
