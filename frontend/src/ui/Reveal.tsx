/** Текст появляется по словам: лёгкий подъём и «наводка на резкость». Для скринридера — обычный текст. */
export function Reveal({ text, className, maxMs = 1100 }: { text: string; className?: string; maxMs?: number }) {
  const words = text.split(/(\s+)/);
  const count = words.filter((w) => w.trim()).length || 1;
  const step = Math.min(26, maxMs / count);
  let i = 0;
  return (
    <span className={`reveal ${className ?? ""}`}>
      <span className="sr-only">{text}</span>
      <span aria-hidden="true">
        {words.map((w, k) =>
          w.trim() ? (
            <span key={k} className="w" style={{ animationDelay: `${i++ * step}ms` }}>
              {w}
            </span>
          ) : (
            w
          ),
        )}
      </span>
    </span>
  );
}
