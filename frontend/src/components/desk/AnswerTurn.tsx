import { useMemo } from "react";
import type { Turn } from "../../hooks/useDesk";
import { followUps } from "../../lib/followups";
import { fieldBoxes, verdictMeta } from "../../lib/format";
import type { Box, DocTypeSummary, FieldResult, PartResult, Source } from "../../types";
import { Icon } from "../../ui/Icon";
import { Avatar } from "./Avatar";
import { FieldsCard } from "./FieldsCard";
import type { Highlight } from "./highlight";
import { PhotoNotice } from "./PhotoNotice";
import { TypeRail } from "./TypeRail";
import { VerdictCard } from "./VerdictCard";

interface Props {
  turn: Turn;
  active: boolean;
  types: DocTypeSummary[];
  busy: boolean;
  activeField: string | null;
  onActivate: () => void;
  onFocusSource: (s: Source, boxes: Box[]) => void;
  onClause: (s: Source) => void;
  onFieldHover: (h: Highlight | null) => void;
  onFieldSelect: (f: FieldResult, p?: PartResult) => void;
  onChooseType: (id: string) => void;
  rules: string[];
  onAsk: (question: string) => void;
  /** карточку можно исправить: это документ текущей сессии */
  editable?: boolean;
  onCorrect?: (unit: string, value: string) => Promise<boolean>;
  /** false — уточняющий вопрос по уже показанному документу: только ответ, карточка документа — в первом ответе */
  docCard?: boolean;
  onShowCard?: () => void;
  onRetake?: () => void;
}

/** Ответ секретаря. Активный — раскрыт целиком, прежние — свёрнуты в строку (плавно, без прыжков). */
export function AnswerTurn(p: Props) {
  const { turn } = p;
  const result = turn.result!;
  const meta = verdictMeta(result.answer.verdict, result.answer.intent);
  const next = useMemo(() => followUps(result, turn.question, p.rules), [result, turn.question, p.rules]);

  const resolveBoxes = useMemo(
    () => (s: Source): Box[] => {
      if (s.boxes.length) return s.boxes;
      const f = result.fields.find((x) => x.id === s.ref);
      return f ? fieldBoxes(f) : [];
    },
    [result.fields],
  );

  return (
    <article className={`turn turn-bot answer ${p.active ? "is-active" : "is-folded"}`}>
      <Avatar />
      <div className="bubble bot-bubble">
        <button type="button" className={`fold-summary tone-${meta.tone}`} onClick={p.onActivate} aria-expanded={p.active} tabIndex={p.active ? -1 : 0}>
          <span className="fold-dot" aria-hidden="true" />
          <strong>{meta.title}</strong>
          <span className="fold-text">{result.answer.text}</span>
          <Icon name="chevronDown" size={14} />
        </button>
        <div className="fold" aria-hidden={!p.active} inert={!p.active}>
          <div className="fold-inner">
            {p.docCard !== false && result.photo_quality && <PhotoNotice quality={result.photo_quality} onRetake={p.onRetake} />}
            <VerdictCard
              key={turn.revision ?? 0}
              answer={result.answer}
              warnings={result.warnings}
              images={result.images}
              resolveBoxes={resolveBoxes}
              onFocusSource={(s) => p.onFocusSource(s, resolveBoxes(s))}
              onClause={p.onClause}
            />
            {next.length > 0 && (
              <div className="followups" role="group" aria-label="Спросить дальше">
                <span className="eyebrow">Спросить дальше</span>
                <div className="chips">
                  {next.map((q, i) => (
                    <button key={q} type="button" className="fu" disabled={p.busy} style={{ ["--i" as string]: i }} onClick={() => p.onAsk(q)}>
                      <Icon name="arrowRight" size={13} />
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            )}
            {p.docCard === false ? (
              <p className="card-ref">
                <Icon name="layers" size={13} />
                <span>Карточка документа — в первом ответе по этому файлу.</span>
                {p.onShowCard && (
                  <button type="button" className="link" onClick={p.onShowCard}>
                    Показать
                  </button>
                )}
              </p>
            ) : (
              <>
                <TypeRail info={result.doc_type} types={p.types} busy={p.busy} hasFile={!!turn.file} onChoose={p.onChooseType} />
                <FieldsCard fields={result.fields} activeId={p.activeField} onHover={p.onFieldHover} onSelect={p.onFieldSelect} onCorrect={p.editable ? p.onCorrect : undefined} />
              </>
            )}
          </div>
        </div>
      </div>
    </article>
  );
}
