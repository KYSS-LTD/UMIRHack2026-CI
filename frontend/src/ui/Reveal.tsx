/** Текст реплики. Раньше появлялся по словам (как у чат-ботов); теперь показывается сразу — сама реплика мягко появляется целиком. */
export function Reveal({ text, className }: { text: string; className?: string; maxMs?: number }) {
  return <span className={`reveal ${className ?? ""}`}>{text}</span>;
}
