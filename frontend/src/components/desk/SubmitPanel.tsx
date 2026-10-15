import { useEffect, useId, useState } from "react";
import { api } from "../../api";
import { useAccount } from "../../hooks/useAccount";
import type { Delivery, SubmissionPreview, SubmitResult } from "../../types";
import { Icon } from "../../ui/Icon";

/**
 * Передача обращения в деканат — только по желанию и после подтверждения. Перед отправкой видно, что именно уйдёт;
 * паспортные данные по умолчанию не передаются. Документ всегда можно отнести самому.
 */
export function SubmitPanel({ delivery, onSent }: { delivery: Delivery | null; onSent?: (result: SubmitResult) => void }) {
  const titleId = useId();
  const [open, setOpen] = useState(false);
  const [withPassport, setWithPassport] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [contact, setContact] = useState("");
  const [copied, setCopied] = useState(false);
  const [preview, setPreview] = useState<SubmissionPreview | null>(null);
  const [sending, setSending] = useState(false);
  const [done, setDone] = useState<SubmitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const account = useAccount();

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
      const res = await api.assistant.submit(confirm, withPassport, contact.trim());
      setDone(res);
      if (res.sent && res.ticket_id && !res.duplicate) onSent?.(res);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSending(false);
    }
  };

  const copy = (text: string) => {
    void navigator.clipboard?.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
    });
  };

  /** Номер и код статуса: по ним студент без входа узнаёт, что решил деканат. Код виден, пока жива сессия. */
  const statusCode = (ticketId: number, code: string) => (
    <div className="submit-code">
      <div>
        <span className="eyebrow">Номер и код статуса</span>
        <strong className="tabnum">
          № {ticketId} · {code}
        </strong>
        <small>Сохраните их: по ним на странице «Статус обращения» видно, что решил деканат.</small>
      </div>
      <button type="button" className="btn small" onClick={() => copy(`№ ${ticketId}, код ${code}`)}>
        <Icon name={copied ? "check" : "copy"} size={14} /> {copied ? "Скопировано" : "Копировать"}
      </button>
      <a className="btn small" href={`#/status/${ticketId}`}>
        Проверить статус
      </a>
    </div>
  );

  /** Telegram: вошедшему ответ придёт в чат; остальным — предложение войти (по желанию). Обращение из этой сессии привяжется к аккаунту само. */
  const telegramNote = () => {
    if (account.user) {
      return account.user.notify ? (
        <p className="tg-note ok" role="status">
          <Icon name="bell" size={15} /> Ответ деканата придёт вам в Telegram.
        </p>
      ) : (
        <p className="tg-note">Уведомления в Telegram выключены — статус видно на странице «Статус обращения» и в аккаунте.</p>
      );
    }
    if (account.telegram.login && !account.miniApp) {
      return (
        <div className="tg-note">
          <span>Хотите получить ответ деканата в Telegram? Обращение привяжется к аккаунту само.</span>
          <button type="button" className="btn small" onClick={account.openLogin}>
            <Icon name="telegram" size={14} /> Получать ответ в Telegram
          </button>
        </div>
      );
    }
    return null;
  };

  if (done) {
    return (
      <>
        <p className={`submit-done ${done.sent ? "ok" : ""}`} role="status">
          <Icon name={done.sent ? "check" : "alert"} size={15} />
          <span>
            {done.message}
            {done.ticket_id ? ` Номер обращения: ${done.ticket_id}.` : ""}
          </span>
        </p>
        {done.ticket_id && done.code ? statusCode(done.ticket_id, done.code) : null}
        {done.ticket_id ? telegramNote() : null}
      </>
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

  const fields = Object.entries({ ...(preview?.payload.fields ?? {}), ...(contact.trim() ? { Контакт: contact.trim() } : {}) });
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
      {preview?.already_sent && preview.ticket_code ? statusCode(preview.already_sent, preview.ticket_code) : null}
      {preview?.already_sent ? telegramNote() : null}
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
          <label className="contact-field">
            <span>Как с вами связаться (по желанию)</span>
            <input type="text" value={contact} maxLength={120} onChange={(e) => setContact(e.target.value)} placeholder="почта, телефон или @telegram" autoComplete="off" />
            <small>
              Деканат увидит это в обращении.{" "}
              {preview.mail?.available ? "Если это почта, мы отправим на неё номер и код." : "Письма с кодом сейчас не отправляются — номер и код вы увидите здесь."}
            </small>
          </label>
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
