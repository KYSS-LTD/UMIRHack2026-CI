import type { Verdict } from "../types";

const TEXT: Record<Verdict, string> = { ok: "В порядке", missing: "Не хватает", no_answer: "Ответа нет" };

export function stampDate(d = new Date()): string {
  return d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Круглая-прямоугольная печать деканата. Появляется «ударом»: анимация и шероховатость краски — в CSS. */
export function Stamp({ verdict, date = stampDate(), label }: { verdict: Verdict; date?: string; label?: string }) {
  const text = label ?? TEXT[verdict];
  return (
    <div className={`stamp stamp-${verdict}`} role="img" aria-label={`Штамп: ${text}`}>
      <span className="stamp-ripple" aria-hidden="true" />
      {verdict === "ok" && (
        <span className="stamp-burst" aria-hidden="true">
          {Array.from({ length: 12 }, (_, i) => (
            <i key={i} style={{ ["--a" as string]: `${i * 30}deg`, ["--d" as string]: `${54 + (i % 3) * 14}px` }} />
          ))}
        </span>
      )}
      <div className="stamp-body" aria-hidden="true">
        <small>Деканат · проверка</small>
        <strong>{text}</strong>
        <small>{date}</small>
      </div>
    </div>
  );
}
