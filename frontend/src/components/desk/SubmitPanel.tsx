import { useEffect, useId, useState } from "react";
import { api } from "../../api";
import type { Delivery, SubmissionPreview, SubmitResult } from "../../types";
import { Icon } from "../../ui/Icon";

/**
 * Передача обращения в деканат — только по желанию и после подтверждения. Перед отправкой видно, что именно уйдёт;
 * паспортные данные по умолчанию не передаются. Документ всегда можно отнести самому.
 */
export function SubmitPanel({ delivery }: { delivery: Delivery | null }) {
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [withPassport, setWithPassport] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState<SubmissionPreview | null>(null);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<SubmitResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // предпросмотр нужен сразу: он же говорит, можно ли вообще отправлять (задолженности, неполные данные)
  useEffect(() => {
    let alive = true;
    api.assistant
      .submission(withPassport)
      .then((res) => alive && (setPreview(res), setError(null)))
      .catch((e: Error) => alive && setError(e.message));
    return () => {
      alive = false;
    };
  }, [withPassport]);

  const send = async (confirm: boolean) => {
    setSending(true);
    try {
      setDone(await api.assistant.submit(confirm, withPassport));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  if (done) {
    return (
      <p className={`submit-done ${done.sent ? "ok" : ""}`} role="status">
        <Icon name={done.sent ? "check" : "alert"} size={15} />
        <span>
          {done.message}
          {done.ticket_id ? ` Номер обращения: ${done.ticket_id}.` : ""}
        </span>
      </p>
    );
  }

  const blocked = preview?.blocked ?? [];
  if (blocked.length && !preview?.already_sent) {
    return (
      <p className="submit-done" role="status">
        <Icon name="alert" size={15} />
        <span>Обращение в деканат отправить нельзя. {blocked.join(" ")}</span>
      </p>
    );
  }

  if (!open) {
    return (
      <div className="submit-offer">
        <span>
          {delivery && !delivery.available
            ? `Автоматическая отправка пока недоступна (${delivery.reason}) — передайте документ в деканат лично.`
            : "Документ можно отнести в деканат самому — или передать обращение отсюда."}
        </span>
        {(!delivery || delivery.available) && (
          <button type="button" className="btn small" onClick={() => setOpen(true)}>
            <Icon name="inbox" size={14} /> Передать в деканат…
          </button>
        )}
      </div>
    );
  }

  const fields = Object.entries(preview?.payload.fields ?? {});
  const ready = !!preview && preview.delivery.available && !preview.already_sent;
  return (
    <section className="submit-sheet" aria-labelledby={titleId}>
      <header>
        <p className="eyebrow">Обращение в деканат</p>
        <h4 id={titleId}>{preview ? preview.payload.request : "Готовлю предпросмотр…"}</h4>
      </header>
      {error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
      {preview && !preview.delivery.available && <p className="faint">Автоматическая отправка пока недоступна: {preview.delivery.reason}. Скачайте документ и передайте его лично.</p>}
      {preview?.already_sent && <p className="faint">Обращение уже отправлено (№ {preview.already_sent}).</p>}
      {ready && (
        <>
          <dl className="submit-fields">
            {fields.map(([k, v], i) => (
              <div key={k} style={{ ["--i" as string]: i }}>
                <dt>{k}</dt>
                <dd>{v}</dd>
              </div>
            ))}
          </dl>
          <p className="faint">{preview.attachment ? "К обращению приложится сформированный документ (DOCX)." : "Документ не приложен."}</p>
          {(preview.excluded.length > 0 || withPassport) && (
            <label className="check-line">
              <input type="checkbox" checked={withPassport} onChange={(e) => setWithPassport(e.target.checked)} />
              <span>Передать паспортные данные (по умолчанию не передаются)</span>
            </label>
          )}
          <label className="check-line">
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            <span>Подтверждаю: хочу отправить эти данные в деканат</span>
          </label>
          <div className="submit-actions">
            <button type="button" className="btn primary" disabled={!confirmed || sending} onClick={() => void send(true)}>
              <Icon name="send" size={15} /> Отправить
            </button>
            <button type="button" className="btn ghost" disabled={sending} onClick={() => void send(false)}>
              Не отправлять
            </button>
          </div>
        </>
      )}
    </section>
  );
}
