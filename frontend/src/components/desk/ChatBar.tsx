import { useEffect, useState } from "react";
import { plural } from "../../lib/format";
import { Icon } from "../../ui/Icon";
import { Avatar } from "./Avatar";

interface Props {
  count: number;
  busy: boolean;
  quiz: boolean;
  onClear: () => void;
  /** «Прекратить анкету»: ответы анкеты удаляются, чат возвращается к обычным вопросам */
  onStop: () => void;
  /** документ на столе: имя файла и определённый тип (показываются под заголовком) */
  docName?: string;
  typeTitle?: string | null;
}

/** Полоса «Идёт анкета» под шапкой: одно действие, подтверждение меняется на месте — полоса не меняет высоту и не сдвигает ленту. */
function QuizStrip({ busy, onStop }: { busy: boolean; onStop: () => void }) {
  const [asking, setAsking] = useState(false);

  useEffect(() => {
    if (!asking) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setAsking(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [asking]);

  return (
    <div className="quiz-strip" role="group" aria-label="Анкета">
      <span className="quiz-strip-text" key={asking ? "ask" : "run"}>
        <i aria-hidden="true" />
        {asking ? "Ответы удалятся." : "Идёт анкета"}
      </span>
      {asking ? (
        <span className="quiz-strip-actions" key="ask-actions">
          <button
            type="button"
            className="strip-btn danger"
            disabled={busy}
            autoFocus
            onClick={() => {
              setAsking(false);
              onStop();
            }}
          >
            Прекратить
          </button>
          <button type="button" className="strip-btn" onClick={() => setAsking(false)}>
            Нет
          </button>
        </span>
      ) : (
        <span className="quiz-strip-actions" key="run-actions">
          <button type="button" className="strip-btn" disabled={busy} onClick={() => setAsking(true)} title="Прекратить заполнение анкеты">
            Прекратить анкету
          </button>
        </span>
      )}
    </div>
  );
}

/** Шапка диалога: что сейчас происходит и «Очистить чат»; пока идёт анкета, под ней полоса с «Прекратить анкету». Появляется, как только в ленте есть реплики. */
export function ChatBar({ count, busy, quiz, onClear, onStop, docName, typeTitle }: Props) {
  const status = busy ? "секретарь отвечает…" : `${count} ${plural(count, ["обмен", "обмена", "обменов"])}`;
  const doc = [docName, typeTitle].filter(Boolean).join(" · ");
  return (
    <>
      <header className="chat-bar">
        <span className="chat-avatar">
          <Avatar busy={busy} size={38} />
          <i className={`chat-live ${busy ? "busy" : ""}`} aria-hidden="true" />
        </span>
        <span className="chat-bar-title">
          <strong>Диалог с секретарём</strong>
          <small key={status} aria-live="polite">
            {!busy && <span className="cb-state">на связи</span>}
            <span className="cb-count">{status}</span>
            {doc && (
              <span className="cb-doc" title={doc}>
                <Icon name="doc" size={12} />
                <span>{doc}</span>
              </span>
            )}
          </small>
        </span>
        <button type="button" className="chat-clear" onClick={onClear} title="Очистить чат и начать заново">
          <Icon name="broom" size={15} />
          <span>Очистить чат</span>
        </button>
      </header>
      {quiz && <QuizStrip busy={busy} onStop={onStop} />}
    </>
  );
}
