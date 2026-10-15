import { useEffect, useState } from "react";
import { useAccount } from "../hooks/useAccount";
import { useActivity } from "../lib/activity";
import { Icon } from "../ui/Icon";
import { useToast } from "../ui/Toasts";

const KEY = "dekanat.tgBanner";
/** Через сколько дней напомнить: сначала скоро, потом всё реже — навязчивым баннер быть не должен. */
export const SNOOZE_DAYS = [3, 7, 30];
const DAY = 86_400_000;

interface Snooze {
  until: number;
  n: number;
}

function read(): Snooze {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Snooze> | null;
    if (v && typeof v.until === "number" && typeof v.n === "number") return { until: v.until, n: v.n };
  } catch {
    /* хранилище недоступно или повреждено */
  }
  return { until: 0, n: 0 };
}

export const bannerSnoozed = (now = Date.now()): boolean => read().until > now;

/** «Напомнить позже»: запоминает срок и возвращает, на сколько дней отложено. */
export function snoozeBanner(now = Date.now()): number {
  const s = read();
  const days = SNOOZE_DAYS[Math.min(s.n, SNOOZE_DAYS.length - 1)]!;
  try {
    localStorage.setItem(KEY, JSON.stringify({ until: now + days * DAY, n: s.n + 1 }));
  } catch {
    /* без хранилища баннер просто покажется снова в следующий раз */
  }
  return days;
}

const daysWord = (n: number) => (n === 1 ? "день" : n < 5 ? "дня" : "дней");

/**
 * Предложение войти через Telegram при заходе на сайт. Вход необязателен, поэтому баннер спокойный: пользы три строки и две кнопки —
 * «Войти» и «Напомнить позже». Не показывается вошедшим, внутри самого Telegram и там, где бот не включён.
 */
export function TelegramBanner() {
  const { ready, user, telegram, miniApp, openLogin } = useAccount();
  const toast = useToast();
  const [snoozed, setSnoozed] = useState(() => bannerSnoozed());
  const activity = useActivity();
  const [engaged, setEngaged] = useState(false);
  useEffect(() => {
    if (activity.busy) setEngaged(true); // человек уже работает на столе — баннер не двигает страницу под руками
  }, [activity.busy]);
  if (!ready || user || miniApp || !telegram.login || snoozed || engaged) return null;

  const later = () => {
    const days = snoozeBanner();
    setSnoozed(true);
    toast.show(`Хорошо, напомню через ${days} ${daysWord(days)}. Войти можно и сейчас — в меню справа вверху.`, "info");
  };

  return (
    <aside className="tg-banner" aria-label="Вход через Telegram">
      <span className="tg-ic" aria-hidden="true">
        <Icon name="telegram" size={22} />
      </span>
      <div className="tg-copy">
        <h2>Войдите через Telegram — по желанию</h2>
        <ul>
          <li>Начали анкету на телефоне — закончите на компьютере</li>
          <li>Ответ деканата придёт прямо в Telegram</li>
          <li>Свои обращения видно без кода</li>
        </ul>
        <p className="faint">Без входа всё работает как раньше.</p>
      </div>
      <div className="tg-actions">
        <button type="button" className="btn primary" onClick={openLogin}>
          <Icon name="telegram" size={16} /> Войти через Telegram
        </button>
        <button type="button" className="btn ghost" onClick={later}>
          Напомнить позже
        </button>
      </div>
    </aside>
  );
}
