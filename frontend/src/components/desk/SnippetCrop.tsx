import type { Box } from "../../types";
import { cropAround } from "./highlight";

interface Props {
  src: string;
  width: number;
  height: number;
  boxes: Box[];
  label: string;
  onOpen?: () => void;
}

/** Вырезка снимка с рамкой найденного поля — тот самый «фрагмент фото» из ТЗ, прямо в ответе. */
export function SnippetCrop({ src, width, height, boxes, label, onOpen }: Props) {
  const page = boxes[0]?.page ?? 0;
  const own = boxes.filter((b) => b.page === page);
  const g = cropAround(own, width, height);
  if (!g) return null;
  return (
    <figure className="snippet">
      <button
        type="button"
        className="snippet-img"
        onClick={onOpen}
        title="Показать на снимке"
        aria-label={`Фрагмент снимка: ${label}. Показать на снимке`}
        // размер коробки считается по соотношению сторон вырезки (--r), поэтому снимок не растягивается ни при какой ширине
        style={{ ["--r" as string]: g.w / g.h, backgroundImage: `url(${src})`, backgroundSize: `${(width / g.w) * 100}% ${(height / g.h) * 100}%`, backgroundPosition: `${g.px}% ${g.py}%` }}
      >
        {own.map((b, i) => (
          <span
            key={i}
            className="snippet-box"
            style={{ left: `${((b.x0 - g.x) / g.w) * 100}%`, top: `${((b.y0 - g.y) / g.h) * 100}%`, width: `${((b.x1 - b.x0) / g.w) * 100}%`, height: `${((b.y1 - b.y0) / g.h) * 100}%`, animationDelay: `${i * 35}ms` }}
          />
        ))}
      </button>
      <figcaption>Фрагмент снимка · {label}</figcaption>
    </figure>
  );
}
