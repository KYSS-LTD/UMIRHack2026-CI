import { pointsAttr, quadOf } from "../../lib/quad";
import type { Box } from "../../types";

interface Props {
  boxes: Box[];
  kind: "dim" | "hover" | "hit";
  /** ключ для React: при его смене рамки появляются заново (с анимацией) */
  keyPrefix: string;
  /** размеры листа в пикселях снимка: svg лежит поверх него один к одному */
  width: number;
  height: number;
}

/**
 * Рамки найденного на листе. Снимок прямой — обычные прямоугольники; снимок под углом — наклонные многоугольники в одном svg,
 * чтобы подсветка шла вдоль строки, а не закрашивала описанный прямоугольник.
 */
export function BoxMarks({ boxes, kind, keyPrefix, width, height }: Props) {
  if (!boxes.length) return null;
  const slanted = boxes.some((b) => quadOf(b));
  return (
    <>
      {boxes.map((b, i) =>
        quadOf(b) ? null : <span key={`${keyPrefix}${i}`} className={`box ${kind}`} style={{ left: b.x0, top: b.y0, width: b.x1 - b.x0, height: b.y1 - b.y0, ["--i" as string]: Math.min(i, 24) }} />,
      )}
      {slanted && (
        <svg key={`${keyPrefix}svg`} className="quad-layer" width={width} height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
          {boxes.map((b, i) => {
            const q = quadOf(b);
            return q ? <polygon key={`${keyPrefix}${i}`} className={`quad ${kind}`} points={pointsAttr(q)} style={{ ["--i" as string]: Math.min(i, 24) }} /> : null;
          })}
        </svg>
      )}
    </>
  );
}
