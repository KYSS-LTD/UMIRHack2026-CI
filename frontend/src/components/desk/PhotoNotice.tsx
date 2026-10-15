import type { PhotoQuality } from "../../types";
import { Icon } from "../../ui/Icon";

interface Props {
  quality: PhotoQuality;
  /** «Загрузить другой снимок» (только когда снимок стоит переснять) */
  onRetake?: () => void;
  /** одна строка: для предупреждения под выбранным файлом */
  compact?: boolean;
}

/** Что не так со снимком и как снять лучше. Спокойный тон: это совет, а не ошибка, проверка от него не зависит. */
export function PhotoNotice({ quality, onRetake, compact = false }: Props) {
  const issues = quality.issues.filter((i) => i.severity !== "info");
  if (!issues.length) return null;
  const retake = quality.retake;
  const shown = compact ? issues.slice(0, 1) : issues.slice(0, 3);
  return (
    <aside className={`photo-notice ${retake ? "bad" : "warn"} ${compact ? "compact" : ""}`} role={retake ? "alert" : "note"} aria-label="Качество снимка">
      <Icon name="camera" size={18} />
      <div className="pn-body">
        {!compact && <strong>{retake ? "Снимок лучше переснять" : "Снимок можно улучшить"}</strong>}
        <ul>
          {shown.map((i) => (
            <li key={i.code}>
              <b>{i.title}.</b> {i.hint}
            </li>
          ))}
        </ul>
        {onRetake && retake && !compact && (
          <button type="button" className="btn small" onClick={onRetake}>
            <Icon name="camera" size={14} /> Загрузить другой снимок
          </button>
        )}
      </div>
    </aside>
  );
}
