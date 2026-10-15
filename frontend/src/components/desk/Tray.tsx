import { useRef } from "react";
import { api } from "../../api";
import { ACCEPT, validateFile } from "../../lib/files";
import type { SampleItem } from "../../types";
import { Icon } from "../../ui/Icon";

interface Props {
  maxMb: number;
  samples: SampleItem[];
  dragging: boolean;
  busy: boolean;
  onFile: (f: File) => void;
  onSample: (s: SampleItem) => void;
  onError: (message: string) => void;
}

/** Пустой стол: лоток для документа и «примеры на столе». Принимает файл по клику, перетаскиванию и из буфера. */
export function Tray({ maxMb, samples, dragging, busy, onFile, onSample, onError }: Props) {
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

      {samples.length > 0 && (
        <div className="samples" aria-label="Примеры документов">
          <p className="eyebrow">Или возьмите пример со стола</p>
          <ul role="list">
            {samples.map((s, i) => (
              <li key={s.file} style={{ ["--i" as string]: i, ["--tilt" as string]: `${(i % 2 ? 1 : -1) * (1 + (i % 3))}deg` }}>
                <button type="button" className="sample" disabled={busy} onClick={() => onSample(s)} title={s.title}>
                  <span className="sample-paper">
                    <img src={api.sampleUrl(s.file)} alt="" loading="lazy" draggable={false} />
                  </span>
                  <span className="sample-title">{s.title}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
