import { plural } from "../../lib/format";
import { Icon } from "../../ui/Icon";

interface Props {
  count: number;
  busy: boolean;
  quiz: boolean;
  onClear: () => void;
}

/** Шапка диалога: что сейчас происходит и кнопка «Очистить чат». Появляется, как только в ленте есть реплики. */
export function ChatBar({ count, busy, quiz, onClear }: Props) {
  const status = busy ? "секретарь отвечает…" : quiz ? "идёт анкета" : `${count} ${plural(count, ["обмен", "обмена", "обменов"])}`;
  return (
    <header className="chat-bar">
      <span className={`chat-live ${busy ? "busy" : quiz ? "quiz" : ""}`} aria-hidden="true" />
      <span className="chat-bar-title">
        <strong>Диалог с секретарём</strong>
        <small key={status} aria-live="polite">
          {status}
        </small>
      </span>
      <button type="button" className="chat-clear" onClick={onClear} title="Очистить чат и начать заново">
        <Icon name="broom" size={15} />
        <span>Очистить чат</span>
      </button>
    </header>
  );
}
