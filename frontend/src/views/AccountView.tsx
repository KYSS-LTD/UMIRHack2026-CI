import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { useAccount } from "../hooks/useAccount";
import { fmtDateTime } from "../lib/format";
import type { AccountTicket } from "../types";
import { ConfirmDialog } from "../ui/Dialog";
import { Icon } from "../ui/Icon";
import { useToast } from "../ui/Toasts";
import { StatusSteps } from "./StatusView";

const TITLES: Record<AccountTicket["status"], string> = { new: "Принято", in_progress: "Рассматривается", done: "Рассмотрено", rejected: "Отклонено" };
const isFinal = (s: AccountTicket["status"]) => s === "done" || s === "rejected";

function TicketRow({ t }: { t: AccountTicket }) {
  const [open, setOpen] = useState(false);
  return (
    <li className={`acc-ticket${open ? " open" : ""}`}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="num tabnum">№ {t.id}</span>
        <span className="topic">{t.topic || "Обращение"}</span>
        <span className={`dot ${t.status}`} aria-hidden="true" />
        <span className="state">{TITLES[t.status]}</span>
        <Icon name="chevronDown" size={16} className="chev" />
      </button>
      {open && (
        <div className="acc-ticket-body">
          <StatusSteps status={t.status} />
          <p className="faint">
            Отправлено {fmtDateTime(t.created_at)}
            {t.updated_at !== t.created_at ? ` · обновлено ${fmtDateTime(t.updated_at)}` : ""}
          </p>
          {t.reply ? (
            <blockquote className="status-reply">
              <p className="eyebrow">Ответ деканата{t.reply_at ? ` · ${fmtDateTime(t.reply_at)}` : ""}</p>
              <p>{t.reply}</p>
            </blockquote>
          ) : (
            <p className="muted">Ответа пока нет. Он придёт в Telegram, как только сотрудник ответит.</p>
          )}
        </div>
      )}
    </li>
  );
}

/** Кабинет вошедшего: уведомления, свои обращения без кода, привязка прежнего обращения, выгрузка и удаление данных. */
export function AccountView() {
  const toast = useToast();
  const { ready, user, telegram, openLogin, logout, setUser } = useAccount();
  const [tickets, setTickets] = useState<AccountTicket[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [num, setNum] = useState("");
  const [code, setCode] = useState("");
  const [linking, setLinking] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    try {
      setTickets((await api.account.tickets()).tickets);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  const signed = !!user;
  useEffect(() => {
    if (signed) void load();
  }, [signed, load]);

  // пока есть нерассмотренные обращения, список обновляется раз в минуту (только когда вкладка на виду)
  const pending = !!tickets?.some((t) => !isFinal(t.status));
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(() => document.visibilityState === "visible" && void load(), 60_000);
    return () => clearInterval(t);
  }, [pending, load]);

  const setNotify = async (on: boolean) => {
    try {
      setUser((await api.account.setNotify(on)).user);
    } catch (e) {
      toast.show((e as Error).message, "error");
    }
  };

  const link = async (e: React.FormEvent) => {
    e.preventDefault();
    setLinking(true);
    setLinkError(null);
    try {
      await api.account.linkTicket(Number(num), code);
      setNum("");
      setCode("");
      toast.show("Обращение привязано к аккаунту. Ответы придут в Telegram.", "ok");
      await load();
    } catch (err) {
      setLinkError((err as Error).message);
    } finally {
      setLinking(false);
    }
  };

  const remove = async () => {
    setConfirmDelete(false);
    try {
      await api.account.remove();
      toast.show("Аккаунт удалён вместе с данными. Обращения остались у деканата без привязки к вам.", "ok");
      await logout();
      location.hash = "#/check";
    } catch (e) {
      toast.show((e as Error).message, "error");
    }
  };

  if (!ready) return <div className="account-view" aria-busy="true" />;

  if (!user) {
    return (
      <div className="account-view">
        <header className="view-head">
          <div>
            <p className="eyebrow">Аккаунт</p>
            <h1>Вход через Telegram</h1>
            <p className="muted">Необязательно: без входа всё работает как раньше. Вход нужен, чтобы анкета и документы были общими с ботом, а ответы деканата приходили в Telegram.</p>
          </div>
        </header>
        {telegram.login ? (
          <button type="button" className="btn primary" onClick={openLogin}>
            <Icon name="telegram" size={16} /> Войти через Telegram
          </button>
        ) : (
          <p className="alert">Вход через Telegram на этом сервере не включён. Сайт полностью работает без него.</p>
        )}
      </div>
    );
  }

  return (
    <div className="account-view">
      <header className="view-head acc-head">
        <div className="acc-id">
          <span className="acc-avatar" aria-hidden="true">
            {user.name.trim().charAt(0).toUpperCase() || "?"}
          </span>
          <div>
            <p className="eyebrow">Аккаунт</p>
            <h1>{user.name}</h1>
            <p className="muted">{user.username ? `@${user.username} · ` : ""}вход через Telegram</p>
          </div>
        </div>
        <button type="button" className="btn" onClick={() => void logout()}>
          <Icon name="logout" size={16} /> Выйти
        </button>
      </header>

      <section className="acc-card" aria-labelledby="acc-notify">
        <div className="acc-row">
          <div>
            <h2 id="acc-notify">Уведомления в Telegram</h2>
            <p className="muted">Ответы деканата по вашим обращениям и напоминания, которые вы сами попросили. Больше бот ничего не пишет.</p>
          </div>
          <label className="switch-ctl">
            <input type="checkbox" role="switch" checked={user.notify} onChange={(e) => void setNotify(e.target.checked)} aria-label="Присылать уведомления в Telegram" />
            <span aria-hidden="true" />
          </label>
        </div>
        {telegram.bot && (
          <p className="faint">
            Чтобы получать уведомления, бот должен быть запущен в Telegram:{" "}
            <a href={`https://t.me/${telegram.bot}`} target="_blank" rel="noreferrer">
              @{telegram.bot}
            </a>
            . Там же работают проверка документов, вопросы по Положению и анкета.
          </p>
        )}
      </section>

      <section className="acc-card" aria-labelledby="acc-tickets">
        <h2 id="acc-tickets">Мои обращения</h2>
        {error && (
          <p className="alert" role="alert">
            {error}
          </p>
        )}
        {!tickets && !error && <p className="muted">Загружаю…</p>}
        {tickets && tickets.length === 0 && <p className="muted">Пока нет обращений. Отправить обращение в деканат можно после анкеты — на столе или в боте.</p>}
        {tickets && tickets.length > 0 && (
          <ul className="acc-tickets">
            {tickets.map((t) => (
              <TicketRow key={t.id} t={t} />
            ))}
          </ul>
        )}
        <details className="acc-link">
          <summary>Отправляли обращение раньше, без входа?</summary>
          <form onSubmit={link}>
            <label>
              <span className="eyebrow">Номер</span>
              <input inputMode="numeric" value={num} maxLength={9} onChange={(e) => setNum(e.target.value.replace(/\D/g, ""))} autoComplete="off" />
            </label>
            <label>
              <span className="eyebrow">Код</span>
              <input value={code} maxLength={12} onChange={(e) => setCode(e.target.value.toUpperCase())} placeholder="ABCD-EFGH" autoComplete="off" spellCheck={false} />
            </label>
            <button type="submit" className="btn" disabled={linking || !num || code.replace(/[^A-Z0-9]/g, "").length < 8}>
              Привязать
            </button>
          </form>
          {linkError && (
            <p className="alert" role="alert">
              {linkError}
            </p>
          )}
        </details>
      </section>

      <section className="acc-card" aria-labelledby="acc-data">
        <h2 id="acc-data">Мои данные</h2>
        <p className="muted">Мы храним имя и @имя из Telegram, анкету и карточку проверенного документа, ваши обращения. Телефон и почту не запрашиваем.</p>
        <div className="acc-actions">
          <a className="btn" href={api.account.exportUrl} download="dekanat-account.json">
            <Icon name="download" size={16} /> Скачать мои данные
          </a>
          <button type="button" className="btn danger" onClick={() => setConfirmDelete(true)}>
            <Icon name="trash" size={16} /> Удалить аккаунт
          </button>
        </div>
      </section>

      <ConfirmDialog
        open={confirmDelete}
        title="Удалить аккаунт?"
        message="Вход на всех устройствах закроется, анкета, документ и уведомления будут удалены — и на сайте, и в боте. Уже отправленные обращения останутся у деканата без привязки к вам."
        confirmLabel="Удалить аккаунт"
        danger
        onConfirm={() => void remove()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
