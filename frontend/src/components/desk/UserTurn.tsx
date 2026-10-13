import { useObjectUrl } from "../../hooks/useObjectUrl";
import type { Turn } from "../../hooks/useDesk";
import { AUTO_TYPE, GENERIC_TYPE } from "../../types";
import { Icon } from "../../ui/Icon";

interface Props {
  turn: Turn;
  typeTitle: (id: string) => string;
  onEdit?: (turn: Turn) => void;
}

/** Реплика студента: вопрос, приложенный файл и выбранный тип. */
export function UserTurn({ turn, typeTitle, onEdit }: Props) {
  const thumb = useObjectUrl(turn.file, turn.fileName);
  const typeLabel = turn.docType === AUTO_TYPE ? null : turn.docType === GENERIC_TYPE ? "Другой документ" : typeTitle(turn.docType);
  return (
    <article className="turn turn-user">
      {onEdit && (
        <button type="button" className="edit-q" onClick={() => onEdit(turn)} aria-label="Изменить вопрос и спросить заново" title="Изменить вопрос">
          <Icon name="edit" size={14} />
        </button>
      )}
      <div className="bubble user-bubble">
        {turn.note && (
          <p className="turn-note">
            <Icon name="refresh" size={13} /> {turn.note}
          </p>
        )}
        <p className="user-text">{turn.question || (turn.file ? "Проверь документ целиком" : "")}</p>
        <time className="turn-time" dateTime={new Date(turn.at).toISOString()}>
          {new Date(turn.at).toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" })}
        </time>
        {(turn.fileName || typeLabel) && (
          <div className="user-attach">
            {turn.fileName && (
              <span className="attach-chip" title={turn.fileName}>
                {thumb ? <img src={thumb} alt="" /> : <Icon name="doc" size={14} />}
                <span>{turn.fileName}</span>
              </span>
            )}
            {typeLabel && <span className="attach-chip type">{typeLabel}</span>}
          </div>
        )}
      </div>
    </article>
  );
}
