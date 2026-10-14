import { useEffect, useState } from "react";
import { plural } from "../../lib/format";
import { Icon } from "../../ui/Icon";

interface Props {
  count: number;
  busy: boolean;
  quiz: boolean;
  onClear: () => void;
  /** «Прекратить анкету»: ответы анкеты удаляются, чат возвращается к обычным вопросам */
  onStop: () => void;
}

/** Шапка диалога: что сейчас происходит, «Прекратить анкету» (пока она идёт) и «Очистить чат». Появляется, как только в ленте есть реплики. */
export function ChatBar({ count, busy, quiz, onClear, onStop }: Props) {
  const [asking, setAsking] = useState(false);
  const status = busy ? "секретарь отвечает…" : quiz ? "идёт анкета" : `${count} ${plural(count, ["обмен", "обмена", "обменов"])}`;

  // анкета кончилась (или её прекратили) — вопрос о подтверждении снимается; Esc тоже его снимает
  useEffect(() => {
    if (!quiz) setAsking(false);
  }, [quiz]);
  useEffect(() => {
    if (!asking) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setAsking(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [asking]);

  return (
    <header className="chat-bar">
      <span className={`chat-live ${busy ? "busy" : quiz ? "quiz" : ""}`} aria-hidden="true" />
      <span className="chat-bar-title">
        <strong>Диалог с секретарём</strong>
        <small key={status} aria-live="polite">
          {status}
        </small>
      </span>
      {quiz &&
        (asking ? (
          <span className="chat-stop-ask" role="group" aria-label="Прекратить анкету?">
            <span>Ответы анкеты удалятся.</span>
            <button
              type="button"
              className="chat-stop-yes"
              disabled={busy}
              onClick={() => {
                setAsking(false);
                onStop();
              }}
              autoFocus
            >
              Прекратить
            </button>
            <button type="button" className="chat-stop-no" onClick={() => setAsking(false)}>
              Нет
            </button>
          </span>
        ) : (
          <button type="button" className="chat-stop" disabled={busy} onClick={() => setAsking(true)} title="Прекратить заполнение анкеты">
            <Icon name="x" size={15} />
            <span>Прекратить анкету</span>
          </button>
        ))}
      <button type="button" className="chat-clear" onClick={onClear} title="Очистить чат и начать заново">
        <Icon name="broom" size={15} />
        <span>Очистить чат</span>
      </button>
    </header>
  );
}
