import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useAccount } from "../hooks/useAccount";
import type { LoginStart } from "../types";
import { Dialog } from "../ui/Dialog";
import { Icon } from "../ui/Icon";
import { useToast } from "../ui/Toasts";

type Phase = "starting" | "waiting" | "denied" | "expired" | "error";

const POLL_MS = 2000;

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Вход через бота: сайт получает одноразовую ссылку, человек открывает бота и подтверждает вход там (так чужую ссылку нельзя
 * «подсунуть» тихо), а сайт, опрашивая сервер, входит сам. Пароль и номер телефона не нужны.
 */
export function LoginDialog() {
  const { loginOpen, closeLogin, signedIn, telegram } = useAccount();
  const toast = useToast();
  const [phase, setPhase] = useState<Phase>("starting");
  const [start, setStart] = useState<LoginStart | null>(null);
  const [message, setMessage] = useState("");
  const [left, setLeft] = useState(0);
  const run = useRef(0);

  const begin = useCallback(async () => {
    const id = ++run.current;
    setPhase("starting");
    setStart(null);
    setMessage("");
    try {
      const res = await api.account.loginStart();
      if (id !== run.current) return;
      setStart(res);
      setLeft(res.expires_in ?? 300);
      setPhase("waiting");
    } catch (e) {
      if (id !== run.current) return;
      setMessage((e as Error).message);
      setPhase("error");
    }
  }, []);

  // при открытии окна — новый запрос входа; при закрытии прежний опрос прекращается
  useEffect(() => {
    if (!loginOpen) {
      run.current++;
      return;
    }
    void begin();
    return () => void run.current++;
  }, [loginOpen, begin]);

  // уже вошёл в другой вкладке: просто принимаем
  useEffect(() => {
    if (start?.already && start.user) signedIn(start.user);
  }, [start, signedIn]);

  // опрос подтверждения; вкладка в фоне — не опрашиваем
  const token = start?.token;
  useEffect(() => {
    if (!loginOpen || phase !== "waiting" || !token) return;
    const ctl = new AbortController();
    const id = run.current;
    const tick = async () => {
      if (document.visibilityState !== "visible") return;
      try {
        const res = await api.account.loginPoll(token, ctl.signal);
        if (id !== run.current) return;
        if (res.status === "confirmed") signedIn(res.user, res.moved);
        else if (res.status === "denied") setPhase("denied");
        else if (res.status === "expired") setPhase("expired");
      } catch (e) {
        if ((e as Error).name !== "AbortError") return; // сеть мигнула — следующий опрос через две секунды
      }
    };
    const t = setInterval(() => void tick(), POLL_MS);
    return () => (clearInterval(t), ctl.abort());
  }, [loginOpen, phase, token, signedIn]);

  // обратный отсчёт: ссылка живёт ограниченное время
  useEffect(() => {
    if (!loginOpen || phase !== "waiting") return;
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [loginOpen, phase]);
  useEffect(() => {
    if (phase === "waiting" && left === 0 && start?.token) setPhase("expired");
  }, [left, phase, start]);

  const copy = () =>
    void navigator.clipboard?.writeText(start?.link ?? "").then(
      () => toast.show("Ссылка скопирована — откройте её на телефоне или в Telegram", "ok"),
      () => toast.show("Не удалось скопировать ссылку", "error"),
    );

  return (
    <Dialog open={loginOpen} title="Войти через Telegram" onClose={closeLogin}>
      <div className="login">
        {phase === "starting" && <p className="muted">Готовлю ссылку для входа…</p>}

        {phase === "waiting" && start?.link && (
          <>
            <ol className="login-steps">
              <li>
                <b>Откройте бота</b>
                <span>
                  <a className="btn primary" href={start.link} target="_blank" rel="noreferrer">
                    <Icon name="telegram" size={16} /> Открыть @{start.bot ?? telegram.bot}
                  </a>
                </span>
              </li>
              <li>
                <b>Нажмите «Старт» и «Подтвердить вход»</b>
                <span className="muted">Подтверждение нужно, чтобы никто не вошёл в ваш аккаунт чужой ссылкой.</span>
              </li>
              <li>
                <b>Вернитесь на сайт</b>
                <span className="muted">Вход произойдёт сам.</span>
              </li>
            </ol>
            <p className="login-wait" role="status" aria-live="polite">
              Жду подтверждения · ссылка действует ещё <span className="tabnum">{clock(left)}</span>
            </p>
            <p className="login-alt">
              <button type="button" className="link" onClick={copy}>
                <Icon name="copy" size={14} /> Скопировать ссылку
              </button>{" "}
              — чтобы открыть её на телефоне.
            </p>
          </>
        )}

        {phase === "denied" && (
          <>
            <p role="status">Вход отклонён в Telegram. Если это были вы, попробуйте ещё раз.</p>
            <button type="button" className="btn" onClick={() => void begin()}>
              Попробовать снова
            </button>
          </>
        )}
        {phase === "expired" && (
          <>
            <p role="status">Ссылка устарела. Получите новую — это займёт несколько секунд.</p>
            <button type="button" className="btn primary" onClick={() => void begin()}>
              Получить новую ссылку
            </button>
          </>
        )}
        {phase === "error" && (
          <>
            <p className="alert" role="alert">
              {message || "Не удалось начать вход."}
            </p>
            <button type="button" className="btn" onClick={() => void begin()}>
              Повторить
            </button>
          </>
        )}

        <details className="login-why">
          <summary>Зачем входить и что с данными</summary>
          <ul>
            <li>Вход необязателен: без него сайт работает как раньше.</li>
            <li>Анкета и документы становятся общими для сайта и бота — начать можно в одном месте, закончить в другом.</li>
            <li>Ответы деканата приходят в Telegram, а свои обращения видно без кода.</li>
            <li>Мы получаем только имя и @имя из Telegram — телефон и почту не запрашиваем. Удалить аккаунт можно в любой момент.</li>
          </ul>
        </details>
      </div>
    </Dialog>
  );
}
