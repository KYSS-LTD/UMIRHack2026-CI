import { useRef } from "react";
import { ACCEPT, validateFile } from "../../lib/files";
import { Icon } from "../../ui/Icon";

interface Props {
  maxMb: number;
  dragging: boolean;
  busy: boolean;
  onFile: (f: File) => void;
  onError: (message: string) => void;
}

/** Пустой стол: лоток для документа. Принимает файл по клику, перетаскиванию и из буфера. */
export function Tray({ maxMb, dragging, busy, onFile, onError }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const camera = useRef<HTMLInputElement>(null);

  const accept = (f: File | undefined | null) => {
    if (!f) return;
    const problem = validateFile(f, maxMb);
    if (problem) onError(problem);
    else onFile(f);
  };

  return (
    <section className={`tray ${dragging ? "is-over" : ""}`} aria-label="Положите документ на стол">
      <svg className="tray-border" aria-hidden="true" preserveAspectRatio="none">
        <rect x="1.5" y="1.5" width="calc(100% - 3px)" height="calc(100% - 3px)" rx="26" />
      </svg>
      <input ref={input} type="file" hidden accept={ACCEPT} data-testid="file-input" onChange={(e) => (accept(e.target.files?.[0]), (e.target.value = ""))} />
      <input ref={camera} type="file" hidden accept="image/jpeg,image/png" capture="environment" onChange={(e) => (accept(e.target.files?.[0]), (e.target.value = ""))} />

      <div className="sheets" aria-hidden="true">
        <span className="sheet s1" />
        <span className="sheet s2" />
        <span className="sheet s3">
          <i />
          <i />
          <i />
          <i className="short" />
        </span>
      </div>

      <div className="tray-text">
        <h2>{dragging ? "Отпустите — я прочитаю" : "Положите документ на стол"}</h2>
        <p className="muted">
          Фото, скан или PDF до {maxMb} МБ. Перетащите файл сюда, вставьте из буфера (<kbd>Ctrl</kbd>+<kbd>V</kbd>) или сфотографируйте.
        </p>
      </div>

      <div className="row tray-actions">
        <button type="button" className="btn primary" disabled={busy} onClick={() => input.current?.click()}>
          <Icon name="inbox" /> Выбрать файл
        </button>
        <button type="button" className="btn ghost camera-btn" disabled={busy} onClick={() => camera.current?.click()}>
          <Icon name="camera" /> Сфотографировать
        </button>
      </div>

    </section>
  );
}
