/**
 * Мост с Telegram Mini App. Сайт открывается внутри Telegram тем же адресом: Telegram кладёт подписанные данные пользователя
 * в хэш адреса (#tgWebAppData=...). Скрипт Telegram подгружается ТОЛЬКО внутри Telegram — обычные посетители не загружают ничего постороннего.
 */

export interface TgWebApp {
  initData: string;
  colorScheme?: "light" | "dark";
  ready: () => void;
  expand: () => void;
  BackButton?: { show: () => void; hide: () => void; onClick: (cb: () => void) => void; offClick: (cb: () => void) => void };
  setHeaderColor?: (color: string) => void;
  setBackgroundColor?: (color: string) => void;
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TgWebApp };
  }
}

const SDK_URL = "https://telegram.org/js/telegram-web-app.js";
const FLAG = "dekanat.miniapp";

const safeSession = {
  get: (k: string) => {
    try {
      return sessionStorage.getItem(k);
    } catch {
      return null;
    }
  },
  set: (k: string, v: string) => {
    try {
      sessionStorage.setItem(k, v);
    } catch {
      /* хранилище недоступно */
    }
  },
};

/** Подписанная строка из хэша адреса, если страницу открыл Telegram. */
export function initDataFromHash(hash: string = location.hash): string {
  const raw = hash.replace(/^#/, "");
  if (!raw.includes("tgWebAppData=")) return "";
  // хэш Mini App — это query-строка; сама initData внутри закодирована, URLSearchParams раскодирует её один раз, как нужно серверу
  return new URLSearchParams(raw.startsWith("/") ? raw.slice(raw.indexOf("tgWebAppData")) : raw).get("tgWebAppData") ?? "";
}

/** Страницу открыл Telegram (по хэшу или по признаку, запомненному на время этой вкладки). */
export function inMiniApp(): boolean {
  if (typeof location === "undefined") return false;
  if (initDataFromHash()) {
    safeSession.set(FLAG, "1");
    return true;
  }
  return safeSession.get(FLAG) === "1" || /[?&]tg=1\b/.test(location.search);
}

export function getWebApp(): TgWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

let sdk: Promise<TgWebApp | null> | null = null;

/** Подгружает скрипт Telegram (один раз). Если он недоступен — null: вход всё равно возможен по данным из хэша. */
export function loadTelegramSdk(timeoutMs = 4000): Promise<TgWebApp | null> {
  if (getWebApp()) return Promise.resolve(getWebApp());
  sdk ??= new Promise((resolve) => {
    const s = document.createElement("script");
    const done = () => resolve(getWebApp());
    s.src = SDK_URL;
    s.async = true;
    s.onload = done;
    s.onerror = () => resolve(null);
    document.head.appendChild(s);
    setTimeout(done, timeoutMs);
  });
  return sdk;
}

/** Готовит окно Mini App: убирает заставку, раскрывает на весь экран. Возвращает данные для входа. */
export async function bootMiniApp(): Promise<{ initData: string; scheme: "light" | "dark" | null }> {
  document.documentElement.dataset.tg = "1";
  const app = await loadTelegramSdk();
  app?.ready();
  app?.expand();
  const initData = app?.initData || initDataFromHash();
  return { initData, scheme: app?.colorScheme ?? null };
}
