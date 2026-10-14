import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { fmtDateTime, relativeTime } from "../lib/format";
import type { TicketStatus } from "../types";
import { Icon } from "../ui/Icon";
import { useToast } from "../ui/Toasts";

const STEPS = [
  { id: "new", title: "Принято", hint: "Обращение в очереди деканата" },
  { id: "in_progress", title: "Рассматривается", hint: "Сотрудник занимается обращением" },
  { id: "decision", title: "Решение", hint: "Ответ деканата" },
] as const;

/** На каком этапе обращение: 0 — принято, 1 — рассматривается, 2 — решение (рассмотрено или отклонено). */
export const stepOf = (status: TicketStatus["status"]): number => (status === "new" ? 0 : status === "in_progress" ? 1 : 2);
const isFinal = (s: TicketStatus["status"]) => s === "done" || s === "rejected";

/** Три этапа обращения: пройденные отмечены галочкой, текущий выделен, у отклонённого последний этап окрашен как отказ. */
function StatusSteps({ status }: { status: TicketStatus["status"] }) {
  const at = stepOf(status);
  return (
    <ol className="status-steps" aria-label="Этапы обращения">
      {STEPS.map((s, i) => {
        const state = i < at || (i === at && isFinal(status)) ? "done" : i === at ? "now" : "todo";
        const bad = i === 2 && status === "rejected";
        const title = i === 2 ? (status === "done" ? "Рассмотрено" : status === "rejected" ? "Отклонено" : s.title) : s.title;
        return (
          <li key={s.id} className={`${state}${bad ? " bad" : ""}`} aria-current={i === at ? "step" : undefined}>
            <span className="node" aria-hidden="true">
              {state === "done" ? <Icon name={bad ? "x" : "check"} size={14} /> : i + 1}
            </span>
            <span className="name">{title}</span>
            <small>{s.hint}</small>
          </li>
        );
      })}
    </ol>
  );
}

/** Номер из адреса вида #/status/12: подставляется в поле, код вводится вручную (в адрес код не попадает). */
export function StatusView({ initialId }: { initialId: string | null }) {
  const toast = useToast();
  const [id, setId] = useState(initialId && /^\d+$/.test(initialId) ? initialId : "");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<TicketStatus | null>(null);
  const [checkedAt, setCheckedAt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const codeRef = useRef<HTMLInputElement>(null);
  const last = useRef<{ id: number; code: string } | null>(null);

  useEffect(() => {
    if (id) codeRef.current?.focus();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  /** Запрос статуса; тихий (автообновление) при ошибке ничего не показывает и оставляет прежний результат. */
  const fetchStatus = useCallback(async (q: { id: number; code: string }, silent: boolean) => {
    if (!silent) {
      setBusy(true);
      setError(null);
    }
    try {
      const res = await api.assistant.ticketStatus(q.id, q.code);
      last.current = q;
      setResult(res);
      setCheckedAt(Date.now());
    } catch (err) {
      if (!silent) {
        setResult(null);
        setError((err as Error).message);
      }
    } finally {
      if (!silent) setBusy(false);
    }
  }, []);

  const check = (e: React.FormEvent) => {
    e.preventDefault();
    void fetchStatus({ id: Number(id), code }, false);
  };

  // пока обращение не рассмотрено, статус обновляется сам раз в минуту (только когда вкладка на виду)
  const open = !!result && !isFinal(result.status);
  useEffect(() => {
    if (!open) return;
    const timer = setInterval(() => {
      if (document.visibilityState === "visible" && last.current) void fetchStatus(last.current, true);
    }, 60_000);
    return () => clearInterval(timer);
  }, [open, fetchStatus]);

  // подпись «обновлено N мин назад» живёт сама
  useEffect(() => {
    if (!checkedAt) return;
    const t = setInterval(() => setNow(Date.now()), 20_000);
    return () => clearInterval(t);
  }, [checkedAt]);

  const copyNumber = () =>
    void navigator.clipboard?.writeText(String(result?.id)).then(
      () => toast.show(`Номер обращения ${result?.id} скопирован`, "ok"),
      () => toast.show("Не удалось скопировать номер", "error"),
    );

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
            <h2>
              Обращение № {result.id}{" "}
              <button type="button" className="icon-btn" onClick={copyNumber} aria-label="Скопировать номер обращения" title="Скопировать номер">
                <Icon name="copy" size={15} />
              </button>
            </h2>
            <span className={`status-stamp ${result.status}`}>{result.status_title}</span>
          </header>
          <StatusSteps status={result.status} />
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
          <footer className="status-foot">
            <span className="faint">
              {isFinal(result.status) ? "Обращение рассмотрено — статус больше не изменится." : "Статус обновляется сам раз в минуту."} Проверено {checkedAt ? relativeTime(checkedAt, now) : "только что"}.
            </span>
            <button type="button" className="btn small" disabled={busy} onClick={() => last.current && void fetchStatus(last.current, false)}>
              <Icon name="refresh" size={14} /> Обновить
            </button>
          </footer>
        </article>
      )}
    </div>
  );
}
