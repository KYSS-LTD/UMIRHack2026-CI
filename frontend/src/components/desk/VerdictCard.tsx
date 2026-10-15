import { useState } from "react";
import { answerToText } from "../../lib/followups";
import { verdictMeta } from "../../lib/format";
import type { Answer, Box, PagePreview, Source } from "../../types";
import { Icon } from "../../ui/Icon";
import { Reveal } from "../../ui/Reveal";
import { Ring } from "../../ui/Ring";
import { Stamp } from "../../ui/Stamp";
import { useToast } from "../../ui/Toasts";
import { SnippetCrop } from "./SnippetCrop";

interface Props {
  answer: Answer;
  warnings: string[];
  images: PagePreview[];
  /** рамки источника: у «полей» они лежат в карточке, а не в самом источнике */
  resolveBoxes: (s: Source) => Box[];
  onFocusSource: (s: Source) => void;
  onClause: (s: Source) => void;
}

export function VerdictCard({ answer, warnings, images, resolveBoxes, onFocusSource, onClause }: Props) {
  const meta = verdictMeta(answer.verdict, answer.intent);
  const toast = useToast();
  const [openClause, setOpenClause] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    const text = answerToText(meta.title, answer.text, [...answer.sources].map((s) => s.label));
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1600);
      toast.show("Ответ скопирован", "ok");
    } catch {
      toast.show("Не удалось скопировать: разрешите доступ к буферу обмена", "error");
    }
  };
  const notes = [...answer.notes, ...warnings];
  const missing = answer.missing.filter((m) => m.unit !== "document_type");
  const clause = [...answer.sources, ...answer.related].find((s) => s.kind === "clause" && s.ref === openClause);

  const snippets = [...answer.sources, ...answer.related]
    .filter((s) => s.kind !== "clause")
    .map((s) => ({ s, boxes: resolveBoxes(s) }))
    .filter((x) => x.boxes.length && images[x.boxes[0]!.page])
    .slice(0, 2);

  const chip = (s: Source, related = false) => {
    const isClause = s.kind === "clause";
    return (
      <button
        key={`${s.kind}:${s.ref}:${related ? "r" : "m"}`}
        type="button"
        className={`src ${isClause ? "is-clause" : "is-field"} ${related ? "rel" : ""}`}
        aria-expanded={isClause ? openClause === s.ref : undefined}
        title={isClause ? "Показать текст пункта" : "Показать на снимке"}
        onClick={() => (isClause ? setOpenClause((c) => (c === s.ref ? null : s.ref)) : onFocusSource(s))}
      >
        <Icon name={isClause ? "book" : "eye"} size={13} />
        {s.label}
      </button>
    );
  };

  return (
    <section className={`verdict tone-${meta.tone}`} aria-labelledby="verdict-title">
      <header className="verdict-head">
        <div>
          <p className="eyebrow">Заключение</p>
          <h2 id="verdict-title">{meta.title}</h2>
        </div>
        <Stamp verdict={answer.verdict} label={meta.stamp} />
      </header>

      <p className="verdict-text" data-testid="answer-text">
        <Reveal text={answer.text} />
      </p>

      {missing.length > 0 && (
        <ul className="v-missing" aria-label="Чего не хватает">
          {missing.map((m, i) => (
            <li key={`${m.title}${i}`} style={{ ["--i" as string]: i }}>
              <span className="miss-x" aria-hidden="true">
                <Icon name="x" size={12} />
              </span>
              <span>{m.title}</span>
              {m.clause && (
                <button type="button" className="link" onClick={() => onClause({ kind: "clause", ref: String(m.clause), label: `п. ${String(m.clause)}`, quote: "", boxes: [] })}>
                  п. {String(m.clause)}
                </button>
              )}
            </li>
          ))}
        </ul>
      )}

      {snippets.length > 0 && (
        <div className="v-snippets">
          {snippets.map(({ s, boxes }) => (
            <SnippetCrop
              key={`${s.kind}:${s.ref}`}
              src={images[boxes[0]!.page]!.data_url}
              width={images[boxes[0]!.page]!.width}
              height={images[boxes[0]!.page]!.height}
              boxes={boxes}
              label={s.label}
              onOpen={() => onFocusSource(s)}
            />
          ))}
        </div>
      )}

      <footer className="v-foot">
        {(answer.sources.length > 0 || answer.related.length > 0) && (
          <div className="v-sources">
            <span className="eyebrow">Источник</span>
            <div className="chips">
              {answer.sources.map((s) => chip(s))}
              {answer.related.map((s) => chip(s, true))}
            </div>
          </div>
        )}
        <div className="v-conf" title="Насколько уверенно найден ответ">
          <Ring value={answer.confidence} />
          <small className="faint">уверенность</small>
        </div>
      </footer>
      <div className="v-actions">
        <button type="button" className={`link copy-answer ${copied ? "done" : ""}`} onClick={() => void copy()}>
          <Icon name={copied ? "check" : "copy"} size={14} key={copied ? "ok" : "copy"} /> {copied ? "Скопировано" : "Копировать ответ"}
        </button>
      </div>

      {clause && (
        <blockquote className="quote">
          <p>{clause.quote}</p>
          <button type="button" className="link" onClick={() => onClause(clause)}>
            Открыть в Положении <Icon name="arrowRight" size={13} />
          </button>
        </blockquote>
      )}

      {answer.report?.explanation && (
        <p className="v-explain">
          <span className="eyebrow">Пересказ</span> {answer.report.explanation}
        </p>
      )}

      {notes.length > 0 && (
        <ul className="v-notes" aria-label="Примечания">
          {notes.map((n, i) => (
            <li key={i}>
              <Icon name="alert" size={14} />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      )}

      <p className="v-disclaimer faint">{answer.report?.disclaimer ?? "Это предварительная оценка. Окончательное решение принимает сотрудник деканата."}</p>
    </section>
  );
}
