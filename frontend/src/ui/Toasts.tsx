import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react";
import { Icon } from "./Icon";

type Tone = "info" | "ok" | "error";
interface Toast {
  id: number;
  tone: Tone;
  text: string;
  ms: number;
}

interface ToastApi {
  show: (text: string, tone?: Tone) => void;
}

const Ctx = createContext<ToastApi>({ show: () => {} });
export const useToast = () => useContext(Ctx);

const ICON = { info: "alert", ok: "check", error: "alert" } as const;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setItems((l) => l.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (text: string, tone: Tone = "info") => {
      const id = ++seq.current;
      const ms = tone === "error" ? 7000 : 3800;
      setItems((l) => [...l.slice(-3), { id, tone, text, ms }]);
      setTimeout(() => dismiss(id), ms);
    },
    [dismiss],
  );
  const api = useMemo(() => ({ show }), [show]);

  return (
    <Ctx.Provider value={api}>
      {children}
      <div className="toasts" role="region" aria-label="Уведомления" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`} role={t.tone === "error" ? "alert" : "status"} style={{ ["--ms" as string]: `${t.ms}ms` }}>
            <Icon name={ICON[t.tone]} size={18} />
            <span>{t.text}</span>
            <button type="button" className="icon-btn" aria-label="Закрыть уведомление" onClick={() => dismiss(t.id)}>
              <Icon name="x" size={14} />
            </button>
            <i className="toast-timer" aria-hidden="true" />
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
