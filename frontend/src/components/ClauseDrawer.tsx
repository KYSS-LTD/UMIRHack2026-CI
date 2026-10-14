import { useEffect, useMemo, useRef, useState } from "react";
import { useRegulation } from "../hooks/useRegulation";
import { clauseNo } from "../lib/format";
import { Icon } from "../ui/Icon";

interface Props {
  /** номер пункта, который нужно показать; null — панель закрыта */
  refId: string | null;
  /** цитата из ответа: показывается, если пункт не нашёлся в загруженном Положении */
  quote: string;
  onClose: () => void;
  onOpenRegulation: (ref: string) => void;
}

/** Боковая панель с текстом пункта Положения и соседними пунктами: основание ответа видно, не уходя со стола. */
export function ClauseDrawer({ refId, quote, onClose, onOpenRegulation }: Props) {
  const open = refId !== null;
  const { data, error } = useRegulation(open);
  const [cur, setCur] = useState<string | null>(refId);
  const closeBtn = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (refId) setCur(refId);
  }, [refId]);

  useEffect(() => {
    if (!open) return;
    closeBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && (e.stopPropagation(), onClose());
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [open, onClose]);

  const idx = useMemo(() => (data && cur ? data.clauses.findIndex((c) => c.number === cur) : -1), [data, cur]);
  const clause = idx >= 0 ? data!.clauses[idx]! : null;
  const prev = idx > 0 ? data!.clauses[idx - 1]! : null;
  const next = idx >= 0 && idx < data!.clauses.length - 1 ? data!.clauses[idx + 1]! : null;

  return (
    <>
      <div className="drawer-veil" data-open={open} onClick={onClose} aria-hidden="true" />
      <aside className="drawer" data-open={open} role="dialog" aria-modal="false" aria-label="Пункт Положения" inert={!open}>
        <header className="drawer-head">
          <p className="eyebrow">Основание ответа</p>
          <button ref={closeBtn} type="button" className="icon-btn" aria-label="Закрыть панель" onClick={onClose}>
            <Icon name="x" size={16} />
          </button>
        </header>
        <div className="drawer-body" key={cur ?? "none"}>
          {clause ? (
            <>
              {(clause.document || clause.section.length > 0) && <p className="crumbs">{[clause.document ? `Документ «${clause.document.name}»` : "", ...clause.section].filter(Boolean).join(" › ")}</p>}
              <div className="clause-big">
                <span className="num serif tabnum">{clauseNo(clause)}</span>
                <p>{clause.text}</p>
              </div>
            </>
          ) : (
            <div className="clause-big">
              <span className="num serif tabnum">{cur ?? ""}</span>
              <p>{quote || (error ? error : "Загружаю текст пункта…")}</p>
            </div>
          )}
        </div>
        <nav className="drawer-nav" aria-label="Соседние пункты">
          <button type="button" disabled={!prev} onClick={() => prev && setCur(prev.number)}>
            <small>Предыдущий</small>
            <span>{prev ? `п. ${clauseNo(prev)}` : "—"}</span>
          </button>
          <button type="button" disabled={!next} onClick={() => next && setCur(next.number)}>
            <small>Следующий</small>
            <span>{next ? `п. ${clauseNo(next)}` : "—"}</span>
          </button>
        </nav>
        <footer className="drawer-foot">
          <button type="button" className="btn" onClick={() => cur && onOpenRegulation(cur)}>
            <Icon name="book" size={16} /> Открыть в Положении
          </button>
        </footer>
      </aside>
    </>
  );
}
