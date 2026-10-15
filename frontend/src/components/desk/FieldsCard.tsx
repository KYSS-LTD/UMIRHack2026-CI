import { useState } from "react";
import { fieldBoxes, pct } from "../../lib/format";
import type { FieldResult, FieldStatus, PartResult } from "../../types";
import { Icon } from "../../ui/Icon";
import type { Highlight } from "./highlight";

const LABEL: Record<FieldStatus, string> = { found: "найдено", partial: "найдено частично", missing: "не найдено" };

function Glyph({ status }: { status: FieldStatus }) {
  return (
    <span className={`glyph ${status}`} role="img" aria-label={LABEL[status]}>
      {status === "found" && (
        <svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true">
          <path className="draw" d="M5 12.5l4.5 4.5L19 7.5" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      )}
      {status === "partial" && <span aria-hidden="true">!</span>}
      {status === "missing" && <span aria-hidden="true">–</span>}
    </span>
  );
}

interface Props {
  fields: FieldResult[];
  activeId: string | null;
  onHover: (h: Highlight | null) => void;
  onSelect: (field: FieldResult, part?: PartResult) => void;
  /** правка значения (документ текущей сессии): true — сохранено, false — сервер не принял значение */
  onCorrect?: (unit: string, value: string) => Promise<boolean>;
}

interface Editing {
  unit: string;
  title: string;
  mark: boolean;
}

/** Строка правки: значение проходит на сервере ту же проверку формата, что и распознанное. Enter — сохранить, Esc — отменить. */
function Editor({ edit, initial, onSave, onCancel }: { edit: Editing; initial: string; onSave: (v: string) => Promise<void>; onCancel: () => void }) {
  const [value, setValue] = useState(initial);
  const [saving, setSaving] = useState(false);
  return (
    <form
      className="f-editor"
      onSubmit={async (e) => {
        e.preventDefault();
        setSaving(true);
        await onSave(value);
        setSaving(false);
      }}
    >
      <span className="f-title">{edit.title}</span>
      <input
        autoFocus
        value={value}
        maxLength={300}
        aria-label={`Новое значение: ${edit.title}`}
        placeholder={edit.mark ? "есть / нет" : "пусто — «не найдено»"}
        disabled={saving}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && (e.preventDefault(), e.stopPropagation(), onCancel())}
      />
      <button type="submit" className="icon-btn ok" aria-label="Сохранить" title="Сохранить (Enter)" disabled={saving}>
        <Icon name="check" size={15} />
      </button>
      <button type="button" className="icon-btn" aria-label="Отменить" title="Отменить (Esc)" disabled={saving} onClick={onCancel}>
        <Icon name="x" size={15} />
      </button>
    </form>
  );
}

/** Карточка документа из ТЗ: все поля выбранного типа; чего нет — «не найдено». Наведение показывает место на снимке. */
export function FieldsCard({ fields, activeId, onHover, onSelect, onCorrect }: Props) {
  const [editing, setEditing] = useState<Editing | null>(null);
  if (!fields.length) return null;
  const found = fields.filter((f) => f.status === "found").length;
  const share = found / fields.length;
  let n = 0;

  const save = async (value: string) => {
    if (!editing || !onCorrect) return;
    if (await onCorrect(editing.unit, value)) setEditing(null);
  };

  const fixButton = (edit: Editing) =>
    onCorrect && (
      <button type="button" className="f-fix icon-btn" aria-label={`Исправить: ${edit.title}`} title="Исправить значение" onClick={() => setEditing(edit)}>
        <Icon name="edit" size={14} />
      </button>
    );

  return (
    <section className="fields" aria-labelledby="fields-title">
      <header className="fields-head">
        <div>
          <p className="eyebrow">Карточка документа</p>
          <h3 id="fields-title">Что найдено в документе</h3>
        </div>
        <div className="fields-count tabnum" title="Найдено полей">
          <strong>{found}</strong>
          <span> из {fields.length}</span>
        </div>
      </header>
      <div className="fields-meter" aria-hidden="true">
        <i style={{ transform: `scaleX(${share})` }} />
      </div>
      <p className="fields-tip faint">
        {fields.some((f) => fieldBoxes(f).length > 0) && "Нажмите на поле — покажу это место на снимке. "}
        {onCorrect && "Прочитано неверно? Нажмите на карандаш и впишите своё значение — ответ пересчитается."}
      </p>

      <ul className="field-rows" role="list">
        {fields.map((f) => {
          const boxes = fieldBoxes(f);
          const clickable = boxes.length > 0;
          const showParts = f.parts.length > 0 && (f.kind !== "marks" || f.parts.length > 1);
          const hl: Highlight = { id: `field:${f.id}`, label: f.title, boxes };
          // у поля из частей правится каждая часть; у одиночной отметки (подпись, печать) — её единственная часть
          const unit = !f.parts.length ? f.id : !showParts && f.parts.length === 1 ? `${f.id}.${f.parts[0]!.id}` : null;
          const mark = f.kind === "marks";
          const rowEdit = unit && editing?.unit === unit;
          return (
            <li key={f.id} style={{ ["--i" as string]: n++ }} className={`frow ${f.status} ${activeId === f.id ? "active" : ""} ${f.corrected ? "corrected" : ""}`}>
              {rowEdit ? (
                <Editor edit={editing!} initial={mark ? (f.found ? "есть" : "нет") : f.status === "missing" ? "" : f.value} onSave={save} onCancel={() => setEditing(null)} />
              ) : (
                <div className={`frow-line ${unit && onCorrect ? "fixable" : ""}`}>
                  <div
                    className={`frow-main ${clickable ? "clickable" : ""}`}
                    role={clickable ? "button" : undefined}
                    tabIndex={clickable ? 0 : undefined}
                    aria-label={clickable ? `${f.title}: показать на снимке` : undefined}
                    onMouseEnter={() => clickable && onHover(hl)}
                    onMouseLeave={() => onHover(null)}
                    onFocus={() => clickable && onHover(hl)}
                    onBlur={() => onHover(null)}
                    onClick={() => clickable && onSelect(f)}
                    onKeyDown={(e) => {
                      if (clickable && (e.key === "Enter" || e.key === " ")) {
                        e.preventDefault();
                        onSelect(f);
                      }
                    }}
                  >
                    <Glyph status={f.status} />
                    <span className="f-title">{f.title}</span>
                    <span className={`f-value ${f.status === "missing" ? "none" : ""}`}>
                      {f.status === "missing" ? "не найдено" : f.value}
                      {f.corrected && <em className="f-fixed">исправлено</em>}
                      {!f.corrected && f.status !== "missing" && f.kind !== "marks" && f.confidence > 0 && (
                        <i className={`f-conf ${pct(f.confidence) < 55 ? "low" : ""}`} title={`Уверенность распознавания: ${pct(f.confidence)}%`}>
                          <b style={{ width: `${pct(f.confidence)}%` }} />
                        </i>
                      )}
                    </span>
                    {clickable && <Icon name="eye" size={14} className="f-eye" />}
                  </div>
                  {unit && fixButton({ unit, title: f.title, mark })}
                </div>
              )}
              {showParts && (
                <ul className="parts" role="list">
                  {f.parts.map((p) => {
                    const punit = `${f.id}.${p.id}`;
                    const ptitle = `${f.title}: ${p.title}`;
                    if (editing?.unit === punit) {
                      return (
                        <li key={p.id} className="part-line editing">
                          <Editor edit={editing} initial={mark ? (p.found ? "есть" : "нет") : p.found ? p.value : ""} onSave={save} onCancel={() => setEditing(null)} />
                        </li>
                      );
                    }
                    const phl: Highlight = { id: `field:${punit}`, label: ptitle, boxes: p.boxes };
                    const pc = p.boxes.length > 0;
                    return (
                      <li key={p.id} className={`part-line ${onCorrect ? "fixable" : ""}`}>
                        <div
                          className={`part ${p.found ? "found" : "missing"} ${activeId === punit ? "active" : ""} ${pc ? "clickable" : ""}`}
                          role={pc ? "button" : undefined}
                          tabIndex={pc ? 0 : undefined}
                          onMouseEnter={() => pc && onHover(phl)}
                          onMouseLeave={() => onHover(null)}
                          onClick={() => pc && onSelect(f, p)}
                          onKeyDown={(e) => {
                            if (pc && (e.key === "Enter" || e.key === " ")) {
                              e.preventDefault();
                              onSelect(f, p);
                            }
                          }}
                        >
                          <Glyph status={p.found ? "found" : "missing"} />
                          <span className="f-title">{p.title}</span>
                          <span className={`f-value ${p.found ? "" : "none"}`}>
                            {p.found ? p.value : mark ? "нет" : "не найдено"}
                            {p.corrected && <em className="f-fixed">исправлено</em>}
                          </span>
                        </div>
                        {fixButton({ unit: punit, title: ptitle, mark })}
                      </li>
                    );
                  })}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
