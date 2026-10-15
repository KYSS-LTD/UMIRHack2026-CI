import { forwardRef, useEffect, useImperativeHandle, useLayoutEffect, useRef, useState } from "react";
import { useObjectUrl } from "../../hooks/useObjectUrl";
import { ACCEPT, validateFile } from "../../lib/files";
import { formatBytes } from "../../lib/format";
import type { DocTypeSummary, Suggestion } from "../../types";
import { Icon } from "../../ui/Icon";
import { TypePicker } from "./TypePicker";

export interface ComposerHandle {
  focusQuestion: () => void;
  pickFile: () => void;
}

interface Props {
  file: File | null;
  question: string;
  docType: string;
  types: DocTypeSummary[];
  suggestions: Suggestion[];
  maxMb: number;
  busy: boolean;
  onFile: (f: File | null) => void;
  onQuestion: (q: string) => void;
  onDocType: (t: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
  onError: (m: string) => void;
  /** последний заданный вопрос: клавиша ↑ в пустом поле возвращает его, как в консоли */
  recall?: string;
  /** своя подсказка-заглушка (например, пока идёт анкета) вместо сменяющихся вопросов */
  placeholder?: string;
  /** документ в поле уже проверен: подсказка предлагает уточнить, а не «нажмите Проверить» */
  checked?: boolean;
}

const MAX_LEN = 600;

/** Нижняя панель ввода: подсказки, файл, тип документа, вопрос и кнопка отправки. */
export const Composer = forwardRef<ComposerHandle, Props>(function Composer(p, ref) {
  const area = useRef<HTMLTextAreaElement>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [focused, setFocused] = useState(false);
  const [ph, setPh] = useState(0);
  const thumb = useObjectUrl(p.file);
  const canRun = !!(p.file || p.question.trim());

  useImperativeHandle(ref, () => ({ focusQuestion: () => area.current?.focus(), pickFile: () => picker.current?.click() }));

  // поле растёт вместе с текстом, но не больше шести строк
  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 150)}px`;
  }, [p.question]);

  // подсказка-заглушка меняется сама, пока поле пустое
  useEffect(() => {
    if (p.question || focused || p.placeholder || p.suggestions.length < 2) return;
    const id = setInterval(() => setPh((n) => n + 1), 3600);
    return () => clearInterval(id);
  }, [p.question, focused, p.placeholder, p.suggestions.length]);

  const accept = (f: File | undefined | null) => {
    if (!f) return;
    const problem = validateFile(f, p.maxMb);
    if (problem) p.onError(problem);
    else p.onFile(f);
  };

  // подсказка под текущий шаг: что делать сейчас, а не перечень клавиш
  const typing = p.question.trim().length > 0;
  const step = p.busy ? "Идёт проверка — остановить можно кнопкой справа" : !p.file && !typing ? "Приложите документ или задайте вопрос" : p.file && !typing ? (p.checked ? "Документ проверен — можно уточнить вопросом" : "Вопрос необязателен — нажмите «Проверить»") : null;
  const placeholder = p.placeholder ?? (p.suggestions.length ? p.suggestions[ph % p.suggestions.length]!.text : "Задайте вопрос по документу или по Положению");

  return (
    <form
      className={`dock ${focused ? "focused" : ""} ${p.busy ? "busy" : ""}`}
      aria-label="Документ и вопрос"
      onSubmit={(e) => {
        e.preventDefault();
        if (canRun && !p.busy) p.onSubmit();
      }}
    >
      {p.file && (
        <div className="dock-file">
          {thumb ? <img src={thumb} alt="" /> : <span className="pdf-mini">PDF</span>}
          <span className="df-main">
            <strong title={p.file.name}>{p.file.name}</strong>
            <small>{formatBytes(p.file.size)}</small>
          </span>
          <button type="button" className="icon-btn" aria-label="Убрать файл" disabled={p.busy} onClick={() => p.onFile(null)}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}

      <div className="dock-field">
        <textarea
          ref={area}
          rows={1}
          value={p.question}
          maxLength={MAX_LEN}
          aria-label="Вопрос"
          aria-describedby="dock-hint"
          placeholder=""
          onChange={(e) => p.onQuestion(e.target.value)}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={(e) => {
            if (e.key === "ArrowUp" && !p.question && p.recall) {
              e.preventDefault();
              p.onQuestion(p.recall);
              return;
            }
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing && canRun && !p.busy) {
              e.preventDefault();
              p.onSubmit();
            }
          }}
        />
        {!p.question && (
          <span className="ph" key={placeholder} aria-hidden="true">
            {placeholder}
          </span>
        )}
      </div>

      <div className="dock-bar">
        <input ref={picker} type="file" hidden accept={ACCEPT} data-testid="dock-file" onChange={(e) => (accept(e.target.files?.[0]), (e.target.value = ""))} />
        <button type="button" className="round-btn" aria-label="Прикрепить документ" title="Прикрепить документ" disabled={p.busy} onClick={() => picker.current?.click()}>
          <Icon name="attach" size={18} />
        </button>
        <TypePicker value={p.docType} types={p.types} disabled={p.busy} onChange={p.onDocType} />
        <span className="grow" />
        <span id="dock-hint" className="dock-hint faint">
          {p.question.length > MAX_LEN - 80 ? <span className="tabnum">{p.question.length}/{MAX_LEN}</span> : (step ?? <><kbd>Enter</kbd> — отправить<span className="dock-hint-more">, <kbd>Shift</kbd>+<kbd>Enter</kbd> — новая строка</span></>)}
        </span>
        {p.busy ? (
          <button type="button" className="send stop" onClick={p.onCancel} aria-label="Остановить проверку">
            <i className="send-ring" aria-hidden="true" />
            <Icon name="stop" size={14} />
          </button>
        ) : (
          <button type="submit" className="send" disabled={!canRun} aria-label="Проверить" title={canRun ? "Проверить (Enter)" : "Сначала приложите документ или напишите вопрос"}>
            <Icon name="send" size={18} />
          </button>
        )}
      </div>
    </form>
  );
});
