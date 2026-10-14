import { useMemo, useState } from "react";
import { plural } from "../../lib/format";
import { Icon } from "../../ui/Icon";
import { useToast } from "../../ui/Toasts";

interface Props {
  text: string;
  fileName: string;
  /** запросить показ фразы на снимке: вопрос уходит в чат, ответ подсвечивает найденное */
  onFind?: (phrase: string) => void;
  busy?: boolean;
  onClose: () => void;
}

/** Куски текста: совпадения с искомым помечаются, остальное — как есть. Регистр не важен, «ё» = «е». */
export function splitMatches(text: string, phrase: string): { text: string; hit: boolean }[] {
  const needle = phrase.trim();
  if (!needle) return [{ text, hit: false }];
  const fold = (s: string) => s.toLowerCase().replace(/ё/g, "е");
  const hay = fold(text);
  const n = fold(needle);
  const out: { text: string; hit: boolean }[] = [];
  let from = 0;
  for (let at = hay.indexOf(n); at !== -1; at = hay.indexOf(n, from)) {
    if (at > from) out.push({ text: text.slice(from, at), hit: false });
    out.push({ text: text.slice(at, at + n.length), hit: true });
    from = at + n.length;
  }
  if (from < text.length) out.push({ text: text.slice(from), hit: false });
  return out.length ? out : [{ text, hit: false }];
}

/** Распознанный текст документа: прочитать, найти, скопировать или скачать; нужную фразу можно показать на снимке. */
export function OcrTextPanel({ text, fileName, onFind, busy, onClose }: Props) {
  const toast = useToast();
  const [phrase, setPhrase] = useState("");
  const parts = useMemo(() => splitMatches(text, phrase), [text, phrase]);
  const found = parts.filter((p) => p.hit).length;
  const words = text.split(/\s+/).filter(Boolean).length;

  const copy = () => {
    void navigator.clipboard?.writeText(text).then(
      () => toast.show("Текст скопирован", "ok"),
      () => toast.show("Не удалось скопировать", "error"),
    );
  };
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = `${(fileName || "документ").replace(/\.[^.]+$/, "")}-текст.txt`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
    toast.show("Текст сохранён", "ok");
  };

  return (
    <aside className="text-panel" aria-label="Распознанный текст">
      <header>
        <div>
          <strong>Распознанный текст</strong>
          <span className="tabnum">
            {words} {plural(words, ["слово", "слова", "слов"])} · {text.length} зн.
          </span>
        </div>
        <button type="button" className="tool" aria-label="Закрыть текст" title="Закрыть (Esc)" onClick={onClose}>
          <Icon name="x" size={16} />
        </button>
      </header>

      <form
        className="text-find"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          if (phrase.trim() && onFind) onFind(phrase.trim());
        }}
      >
        <Icon name="search" size={15} />
        <input
          type="search"
          value={phrase}
          onChange={(e) => setPhrase(e.target.value)}
          onKeyDown={(e) => e.key === "Escape" && (phrase ? setPhrase("") : onClose())}
          placeholder="Найти в тексте"
          aria-label="Найти в распознанном тексте"
          autoComplete="off"
        />
        {phrase.trim() && (
          <span className="text-count tabnum" aria-live="polite">
            {found ? `${found} ${plural(found, ["совпадение", "совпадения", "совпадений"])}` : "нет совпадений"}
          </span>
        )}
        {onFind && (
          <button type="submit" className="text-show" disabled={!phrase.trim() || busy} title="Подсветить найденное на снимке">
            На снимке
          </button>
        )}
      </form>

      <div className="text-body" tabIndex={0} role="region" aria-label="Текст документа">
        {parts.map((p, i) => (p.hit ? <mark key={i}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
      </div>

      <footer>
        <button type="button" className="tool wide" onClick={copy}>
          <Icon name="copy" size={15} />
          <span>Копировать</span>
        </button>
        <button type="button" className="tool wide" onClick={download}>
          <Icon name="download" size={15} />
          <span>Скачать .txt</span>
        </button>
      </footer>
    </aside>
  );
}
