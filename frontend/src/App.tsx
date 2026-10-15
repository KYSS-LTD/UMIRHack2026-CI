import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { CommandPalette } from "./components/CommandPalette";
import { LoginDialog } from "./components/LoginDialog";
import { NavBar, type Route } from "./components/NavBar";
import { TelegramBanner } from "./components/TelegramBanner";
import { Tour } from "./components/Tour";
import { AccountProvider, useAccount } from "./hooks/useAccount";
import { useFetch } from "./hooks/useFetch";
import { useTheme } from "./hooks/useTheme";
import { useBus } from "./lib/bus";
import { useActivity } from "./lib/activity";
import { pct } from "./lib/format";
import { getWebApp } from "./lib/telegram";
import type { Health } from "./types";
import { Dialog } from "./ui/Dialog";
import { LogoDefs } from "./ui/Logo";
import { ToastProvider } from "./ui/Toasts";
import { AccountView } from "./views/AccountView";
import { CheckView } from "./views/CheckView";
import { RegulationView } from "./views/RegulationView";
import { StatusView } from "./views/StatusView";

export function parseHash(hash: string): { route: Route; clause: string | null } {
  const m = hash.replace(/^#\/?/, "").split("/");
  const route = m[0] === "rules" ? "rules" : m[0] === "status" ? "status" : m[0] === "account" ? "account" : "check";
  return { route, clause: (route === "rules" || route === "status") && m[1] ? decodeURIComponent(m[1]) : null }; // у «status» второй сегмент — номер обращения
}

const SHORTCUTS: [string, string][] = [
  ["Ctrl + K", "Поиск и команды: переходы, примеры, пункты Положения"],
  ["Alt + 1 / 2", "Стол, Положение"],
  ["Enter", "Отправить вопрос (Shift + Enter — новая строка)"],
  ["Ctrl + V", "Вставить снимок из буфера обмена"],
  ["/", "Перейти к полю вопроса"],
  ["Esc", "Остановить идущую проверку или закрыть панель"],
  ["+  −  0  ← ↑ → ↓", "Снимок в фокусе: масштаб, «вписать», сдвиг"],
  ["Двойной щелчок", "Снимок: приблизить место (повторный — вернуть лист)"],
  ["?", "Показать эту подсказку"],
];

/** «Заменить Положение» видит только вошедший сотрудник: обычному посетителю кнопка не нужна, даже если сервер запущен в открытом режиме. */
export const staffCanEdit = (h: Health | null | undefined): boolean => !!h?.admin?.authenticated && (h.can_edit ?? h.allow_edit ?? false);

function Shell() {
  const theme = useTheme();
  const [hash, setHash] = useState(() => parseHash(location.hash));
  const [help, setHelp] = useState(false);
  const [palette, setPalette] = useState(false);
  const [tour, setTour] = useState(false);
  const [tourNew, setTourNew] = useState(() => {
    try {
      return localStorage.getItem("dekanat.tourSeen") !== "1";
    } catch {
      return false; // без хранилища точку «новое» не показываем: она бы не пропала
    }
  });
  const closeTour = useCallback(() => {
    setTour(false);
    setTourNew(false);
    try {
      localStorage.setItem("dekanat.tourSeen", "1");
    } catch {
      /* не страшно */
    }
  }, []);
  const health = useFetch(api.health);
  const types = useFetch(api.doctypes);
  const samples = useFetch(api.samples);
  const activity = useActivity();
  const account = useAccount();

  // внутри Telegram: цвета берём у Telegram (один раз за сессию), а «назад» в шапке Telegram возвращает на стол
  useEffect(() => {
    if (!account.miniApp || !account.scheme || theme.theme !== "system") return;
    try {
      if (sessionStorage.getItem("dekanat.tgTheme")) return;
      sessionStorage.setItem("dekanat.tgTheme", "1");
    } catch {
      /* без хранилища тема применится при каждой загрузке */
    }
    theme.set(account.scheme);
  }, [account.miniApp, account.scheme, theme]);

  useEffect(() => {
    const back = getWebApp()?.BackButton;
    if (!account.miniApp || !back) return;
    const toDesk = () => void (location.hash = "#/check");
    if (hash.route === "check") back.hide();
    else (back.show(), back.onClick(toDesk));
    return () => back.offClick(toDesk);
  }, [account.miniApp, account.ready, hash.route]);

  useEffect(() => {
    const onHash = () => setHash(parseHash(location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((o) => !o);
      } else if (e.altKey && !e.ctrlKey && !e.metaKey && ["1", "2"].includes(e.key)) {
        e.preventDefault();
        location.hash = `#/${(["check", "rules"] as const)[Number(e.key) - 1]}`;
      } else if (e.key === "?" && !typing) setHelp(true);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useBus("help", () => setHelp(true));
  useBus("tour", () => setTour(true));

  // вкладка браузера показывает ход проверки, даже если окно в фоне
  useEffect(() => {
    document.title = activity.busy ? `Проверяю… ${pct(activity.fraction)}% — Онлайн-деканат` : "Онлайн-деканат";
  }, [activity.busy, activity.fraction]);

  const navigate = useCallback((route: Route, clause?: string) => {
    const next = `#/${route}${clause ? `/${encodeURIComponent(clause)}` : ""}`;
    if (location.hash === next) setHash(parseHash(next));
    else location.hash = next;
  }, []);
  const openClause = useCallback((ref: string) => navigate("rules", ref), [navigate]);

  const typeList = types.data ?? [];
  const editable = staffCanEdit(health.data);
  const ocrDown = health.data?.ocr?.available === false;
  const staff = !!health.data?.admin?.authenticated;
  const nav = {
    user: account.user,
    canLogin: account.telegram.login && !account.miniApp,
    onLogin: account.openLogin,
    onLogout: () => void account.logout(),
  };

  return (
    <>
      <LogoDefs />
      <div className="ambient" aria-hidden="true">
        <i className="blob b1" />
        <i className="blob b2" />
        <i className="blob b3" />
      </div>
      <svg width="0" height="0" aria-hidden="true" focusable="false" style={{ position: "absolute" }}>
        <filter id="ink-rough" x="-5%" y="-5%" width="110%" height="110%">
          <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" seed="4" result="noise" />
          <feDisplacementMap in="SourceGraphic" in2="noise" scale="2.4" xChannelSelector="R" yChannelSelector="G" />
        </filter>
      </svg>

      <a className="skip" href="#main">
        К содержимому
      </a>
      <NavBar route={hash.route} onRoute={(r) => setHash({ route: r, clause: null })} theme={theme.theme} onThemeSet={theme.set} staff={!!health.data?.admin?.authenticated} onPalette={() => setPalette(true)} onHelp={() => setHelp(true)} onTour={() => setTour(true)} tourNew={tourNew} account={account.telegram.login || account.user ? nav : undefined} />

      <main id="main" tabIndex={-1} className={`route route-${hash.route}`}>
        {health.error && (
          <p className="banner bad" role="alert">
            Нет связи с сервером. Проверьте подключение и повторите.{" "}
            <button type="button" className="link" onClick={() => (health.reload(), types.reload())}>
              Повторить
            </button>
          </p>
        )}
        {ocrDown && (
          <p className="banner bad" role="alert">
            Проверка документов сейчас недоступна: не удалось запустить распознавание текста. Вопросы по Положению и анкета работают — обратитесь в деканат, если проверка нужна срочно.
            {staff && (
              <>
                {" "}
                <a href="/admin">Что делать — в кабинете сотрудника</a>
              </>
            )}
          </p>
        )}
        {hash.route === "check" && <TelegramBanner />}
        <div className="route-body" key={hash.route}>
          {hash.route === "check" && <CheckView types={typeList} samples={samples.data ?? []} health={health.data} onOpenClause={openClause} />}
          {hash.route === "rules" && <RegulationView focusClause={hash.clause} editable={editable} />}
          {hash.route === "status" && <StatusView initialId={hash.clause} />}
          {hash.route === "account" && <AccountView />}
        </div>
      </main>

      <CommandPalette open={palette} onClose={() => setPalette(false)} samples={samples.data ?? []} onNavigate={navigate} onTheme={theme.cycle} account={account.telegram.login || account.user ? nav : undefined} />
      <LoginDialog />
      <Tour open={tour} route={hash.route} onClose={closeTour} />

      <Dialog open={help} title="Горячие клавиши" onClose={() => setHelp(false)}>
        <dl className="shortcuts">
          {SHORTCUTS.map(([k, d]) => (
            <div key={k}>
              <dt>
                <kbd>{k}</kbd>
              </dt>
              <dd>{d}</dd>
            </div>
          ))}
        </dl>
        <p className="hint">Секретарь даёт предварительную оценку. Окончательное решение принимает сотрудник деканата.</p>
      </Dialog>
    </>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AccountProvider>
        <Shell />
      </AccountProvider>
    </ToastProvider>
  );
}
