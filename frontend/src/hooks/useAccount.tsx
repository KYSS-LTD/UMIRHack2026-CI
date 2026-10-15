import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { emit } from "../lib/bus";
import { bootMiniApp, inMiniApp } from "../lib/telegram";
import type { AccountMe, AccountUser, TelegramInfo } from "../types";
import { useToast } from "../ui/Toasts";

const OFF: TelegramInfo = { enabled: false, login: false, bot: null, mini_app: false };

export interface AccountApi {
  /** загрузка закончилась (успешно или нет) */
  ready: boolean;
  user: AccountUser | null;
  telegram: TelegramInfo;
  /** страницу открыл Telegram (Mini App): вход уже выполнен автоматически */
  miniApp: boolean;
  /** цветовая схема Telegram, если страница открыта в нём */
  scheme: "light" | "dark" | null;
  loginOpen: boolean;
  openLogin: () => void;
  closeLogin: () => void;
  signedIn: (user: AccountUser, moved?: { dialog: boolean; document: boolean }) => void;
  logout: () => Promise<void>;
  setUser: (user: AccountUser | null) => void;
  refresh: () => Promise<void>;
}

const Ctx = createContext<AccountApi>({
  ready: true,
  user: null,
  telegram: OFF,
  miniApp: false,
  scheme: null,
  loginOpen: false,
  openLogin: () => {},
  closeLogin: () => {},
  signedIn: () => {},
  logout: async () => {},
  setUser: () => {},
  refresh: async () => {},
});

export const useAccount = () => useContext(Ctx);

/**
 * Необязательный аккаунт. Без входа сайт работает как раньше, поэтому любая ошибка здесь тихая: аккаунт — надстройка, а не условие.
 * Внутри Telegram (Mini App) вход происходит сам: подписанные данные Telegram обмениваются на сессию сайта.
 */
export function AccountProvider({ children }: { children: ReactNode }) {
  const toast = useToast();
  const [me, setMe] = useState<AccountMe | null>(null);
  const [ready, setReady] = useState(false);
  const [loginOpen, setLoginOpen] = useState(false);
  const [scheme, setScheme] = useState<"light" | "dark" | null>(null);
  const miniApp = useRef(inMiniApp()).current;
  if (miniApp) document.documentElement.dataset.tg = "1"; // оформление внутри Telegram (CSS: [data-tg])

  const refresh = useCallback(async () => {
    try {
      setMe(await api.account.me());
    } catch {
      setMe(null);
    } finally {
      setReady(true);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      let signed: AccountUser | null = null;
      if (miniApp) {
        const boot = await bootMiniApp();
        if (!alive) return;
        setScheme(boot.scheme);
        if (boot.initData) {
          try {
            signed = (await api.account.webapp(boot.initData)).user;
          } catch {
            /* вход из приложения не удался — остаёмся обычным посетителем */
          }
        }
      }
      if (!alive) return;
      await refresh();
      if (signed && alive) emit("account", "in");
    })();
    return () => {
      alive = false;
    };
  }, [miniApp, refresh]);

  const signedIn = useCallback(
    (user: AccountUser, moved?: { dialog: boolean; document: boolean }) => {
      setMe((m) => ({ user, telegram: m?.telegram ?? OFF }));
      setLoginOpen(false);
      toast.show(moved?.dialog || moved?.document ? `Вы вошли, ${user.name}. Начатое на сайте перенесено в аккаунт.` : `Вы вошли, ${user.name}. Анкета и документы теперь общие с ботом.`, "ok");
      emit("account", "in");
    },
    [toast],
  );

  const logout = useCallback(async () => {
    try {
      await api.account.logout();
    } catch (e) {
      toast.show((e as Error).message, "error");
      return;
    }
    setMe((m) => (m ? { ...m, user: null } : m));
    toast.show("Вы вышли. Данные аккаунта остались — они вернутся при следующем входе.", "info");
    emit("account", "out");
  }, [toast]);

  const setUser = useCallback((user: AccountUser | null) => setMe((m) => (m ? { ...m, user } : m)), []);
  const openLogin = useCallback(() => setLoginOpen(true), []);
  const closeLogin = useCallback(() => setLoginOpen(false), []);

  const value = useMemo<AccountApi>(
    () => ({ ready, user: me?.user ?? null, telegram: me?.telegram ?? OFF, miniApp, scheme, loginOpen, openLogin, closeLogin, signedIn, logout, setUser, refresh }),
    [ready, me, miniApp, scheme, loginOpen, openLogin, closeLogin, signedIn, logout, setUser, refresh],
  );
  // в Telegram сначала завершается вход из приложения: параллельный запрос стола создал бы анонимную сессию и мог бы перебить cookie входа
  return <Ctx.Provider value={value}>{miniApp && !ready ? null : children}</Ctx.Provider>;
}
