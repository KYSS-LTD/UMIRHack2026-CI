import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtDateTime } from "../lib/format";
import type { TicketStatus } from "../types";
import { Icon } from "../ui/Icon";

/** Номер из адреса вида #/status/12: подставляется в поле, код вводится вручную (в адрес код не попадает). */
export function StatusView({ initialId }: { initialId: string | null }) {
  const [id, setId] = useState(initialId && /^\d+$/.test(initialId) ? initialId : "");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TicketStatus | null>(null);
  const codeRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (id) codeRef.current?.focus();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const check = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.assistant.ticketStatus(Number(id), code));
    } catch (err) {
      setResult(null);
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="status-view">
      <header className="view-head">
        <div>
          <p className="eyebrow">Обращения</p>
          <h1>Статус обращения</h1>
          <p className="muted">Номер и код вы получили, когда отправили обращение в деканат. Вход и регистрация не нужны.</p>
        </div>
      </header>

      <form className="status-form" onSubmit={check}>
        <label>
          <span className="eyebrow">Номер обращения</span>
          <input inputMode="numeric" pattern="[0-9]*" maxLength={9} value={id} onChange={(e) => setId(e.target.value.replace(/\D/g, ""))} placeholder="например, 12" autoComplete="off" />
        </label>
        <label>
          <span className="eyebrow">Код</span>
          <input ref={codeRef} value={code} maxLength={12} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCD-EFGH" autoComplete="off" spellCheck={false} />
        </label>
        <button type="submit" className="btn primary" disabled={busy || !id || code.replace(/[^A-Z0-9]/g, "").length < 8}>
          <Icon name="search" size={16} /> {busy ? "Проверяю…" : "Узнать статус"}
        </button>
      </form>

      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}

      {result && (
        <article className="status-card" aria-live="polite">
          <header>
            <h2>Обращение № {result.id}</h2>
            <span className={`status-stamp ${result.status}`}>{result.status_title}</span>
          </header>
          <dl>
            <div>
              <dt>Тема</dt>
              <dd>{result.topic || "—"}</dd>
            </div>
            <div>
              <dt>Отправлено</dt>
              <dd>{fmtDateTime(result.created_at)}</dd>
            </div>
            <div>
              <dt>Обновлено</dt>
              <dd>{fmtDateTime(result.updated_at)}</dd>
            </div>
          </dl>
          {result.reply ? (
            <blockquote className="status-reply">
              <p className="eyebrow">Ответ деканата{result.reply_at ? ` · ${fmtDateTime(result.reply_at)}` : ""}</p>
              <p>{result.reply}</p>
            </blockquote>
          ) : (
            <p className="muted">Ответа пока нет. Окончательное решение принимает сотрудник деканата — загляните сюда позже.</p>
          )}
        </article>
      )}
    </div>
  );
}
