import { useEffect, useId, useRef, useState } from "react";
import { AUTO_TYPE, type DocTypeSummary, GENERIC_TYPE } from "../../types";
import { Icon } from "../../ui/Icon";

interface Props {
  value: string;
  types: DocTypeSummary[];
  disabled?: boolean;
  onChange: (id: string) => void;
}

/** Выбор типа документа (ручной выбор из ТЗ): всплывающий список с клавиатурной навигацией. */
export function TypePicker({ value, types, disabled, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const options = [
    { id: AUTO_TYPE, title: "Определить автоматически", hint: "по тексту документа" },
    ...types.map((t) => ({ id: t.id, title: t.title, hint: `${t.fields.length} полей` })),
    { id: GENERIC_TYPE, title: "Другой документ", hint: "ответ по его тексту" },
  ];
  const current = options.find((o) => o.id === value) ?? options[0]!;
  const [active, setActive] = useState(0);
  const root = useRef<HTMLDivElement>(null);
  const listId = useId();

  useEffect(() => {
    if (!open) return;
    setActive(Math.max(0, options.findIndex((o) => o.id === value)));
    const onDown = (e: MouseEvent) => !root.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const choose = (id: string) => {
    onChange(id);
    setOpen(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!open) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        setOpen(true);
      }
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopPropagation();
      setOpen(false);
    } else if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => (a + 1) % options.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => (a - 1 + options.length) % options.length);
    } else if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      choose(options[active]!.id);
    } else if (e.key === "Tab") setOpen(false);
  };

  return (
    <div className="picker" ref={root} onKeyDown={onKeyDown}>
      <button
        type="button"
        className={`pill-btn ${value !== AUTO_TYPE ? "set" : ""}`}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-label={`Тип документа: ${current.title}`}
        title="Тип документа: по умолчанию определяется сам, при необходимости выберите вручную"
        onClick={() => setOpen((o) => !o)}
      >
        <Icon name={value === AUTO_TYPE ? "doc" : "layers"} size={15} />
        <span>{current.title}</span>
        <Icon name="chevronDown" size={14} className="chev" />
      </button>
      <ul id={listId} role="listbox" aria-label="Тип документа" className="menu" data-open={open}>
        {options.map((o, i) => (
          <li key={o.id} role="option" aria-selected={o.id === value} className={`${i === active ? "active" : ""} ${o.id === value ? "on" : ""}`} onMouseEnter={() => setActive(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose(o.id)} style={{ ["--i" as string]: i }}>
            <span className="opt-main">
              <strong>{o.title}</strong>
              <small>{o.hint}</small>
            </span>
            {o.id === value && <Icon name="check" size={15} />}
          </li>
        ))}
      </ul>
    </div>
  );
}
