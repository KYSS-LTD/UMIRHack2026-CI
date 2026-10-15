import { useState } from "react";
import { pct } from "../../lib/format";
import { type DocTypeInfo, type DocTypeSummary, GENERIC_TYPE } from "../../types";
import { Icon } from "../../ui/Icon";

interface Props {
  info: DocTypeInfo;
  types: DocTypeSummary[];
  busy: boolean;
  hasFile: boolean;
  onChoose: (typeId: string) => void;
}

/** «Тип документа»: как определён и в один клик — пересчитать как другой (ручной выбор из ТЗ). */
export function TypeRail({ info, types, busy, hasFile, onChoose }: Props) {
  const [open, setOpen] = useState(false);
  if (!hasFile) return null;
  const generic = info.how === "generic";
  const none = !info.id && !generic;
  const selected = generic ? GENERIC_TYPE : info.id;
  const share = new Map(info.candidates.map((c) => [c.id, c.share]));

  const how = none
    ? "не похож ни на один из известных — выберите тип"
    : generic
      ? "ответ по тексту самого документа"
      : info.how === "manual"
        ? "выбран вручную"
        : info.uncertain
          ? `определён неуверенно (${pct(info.confidence)}%) — проверьте`
          : `определён автоматически (${pct(info.confidence)}%)`;

  // тип определён уверенно — достаточно одной строки; список типов раскрывается по «Изменить» или сам, если нужна проверка
  const expanded = open || info.uncertain || none;
  const unique = [...types.map((t) => ({ id: t.id, title: t.title })), { id: GENERIC_TYPE, title: "Другой документ" }];

  return (
    <section className={`typerail ${info.uncertain || none ? "attention" : ""}`} aria-label="Тип документа">
      <div className="tr-head">
        <p className="eyebrow">Тип документа</p>
        <p className="tr-how">
          {!none && <strong>{generic ? "Другой документ" : info.title}</strong>} {none ? how : `— ${how}`}
        </p>
        {!expanded && (
          <button type="button" className="link" disabled={busy} onClick={() => setOpen(true)}>
            Изменить
          </button>
        )}
      </div>
      {expanded && (
      <div className="tr-chips" role="radiogroup" aria-label="Выбрать тип документа">
        {unique.map((o) => {
          const on = o.id === selected;
          const s = share.get(o.id);
          return (
            <button key={o.id} type="button" role="radio" aria-checked={on} className={`tr-chip ${on ? "on" : ""}`} disabled={busy} onClick={() => !on && onChoose(o.id)} title={on ? "Текущий тип" : `Пересчитать как «${o.title}»`}>
              {on && <Icon name="check" size={13} />}
              <span>{o.title}</span>
              {!on && s !== undefined && info.how === "auto" && <small className="tabnum">{pct(s)}%</small>}
            </button>
          );
        })}
      </div>
      )}
    </section>
  );
}
