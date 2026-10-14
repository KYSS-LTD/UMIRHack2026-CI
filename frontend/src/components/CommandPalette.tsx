import { useEffect, useMemo, useRef, useState } from "react";
import { emit } from "../lib/bus";
import { rank } from "../lib/fuzzy";
import { useRegulation } from "../hooks/useRegulation";
import type { SampleItem } from "../types";
import { Icon, type IconName } from "../ui/Icon";
import type { Route } from "./NavBar";

interface Command {
  id: string;
  title: string;
  hint?: string;
  group: string;
  icon: IconName;
  keywords?: string;
  run: () => void;
}

interface Props {
  open: boolean;
  onClose: () => void;
  samples: SampleItem[];
  onNavigate: (route: Route, clause?: string) => void;
  onTheme: () => void;
}

/** Ctrl+K: переходы, действия, примеры и поиск по пунктам Положения в одной строке. */
export function CommandPalette({ open, onClose, samples, onNavigate, onTheme }: Props) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const { data: regulation } = useRegulation(open);

  useEffect(() => {
    if (open) {
      setQuery("");
      setActive(0);
      requestAnimationFrame(() => input.current?.focus());
    }
  }, [open]);

  const base = useMemo<Command[]>(() => {
    const go = (route: Route) => () => (onNavigate(route), onClose());
    const act = (fn: () => void) => () => (onClose(), fn());
    return [
      { id: "go-check", title: "Перейти на стол", group: "Перейти", icon: "doc", keywords: "проверка документ главная", run: go("check") },
      { id: "go-rules", title: "Открыть Положение", group: "Перейти", icon: "book", keywords: "правила пункты", run: go("rules") },
      { id: "new", title: "Очистить чат и стол", group: "Действия", icon: "broom", keywords: "сбросить очистить заново новый вопрос диалог", run: act(() => (onNavigate("check"), emit("new-desk"))) },
      { id: "pick", title: "Выбрать файл с документом", group: "Действия", icon: "inbox", keywords: "загрузить файл фото скан pdf", run: act(() => (onNavigate("check"), setTimeout(() => emit("pick-file"), 60))) },
      { id: "ask", title: "Задать вопрос", group: "Действия", icon: "question", keywords: "ввод фокус", run: act(() => (onNavigate("check"), setTimeout(() => emit("focus-question"), 60))) },
      { id: "form-transfer", title: "Анкета: заявление о переводе", group: "Действия", icon: "edit", keywords: "перевод анкета заявление docx шаблон", run: act(() => (onNavigate("check"), setTimeout(() => emit("say", "Я хочу перевестись"), 80))) },
      { id: "form-enroll", title: "Анкета: поступление и зачисление", group: "Действия", icon: "edit", keywords: "поступить зачисление анкета заявление", run: act(() => (onNavigate("check"), setTimeout(() => emit("say", "Как к вам поступить?"), 80))) },
      { id: "forget", title: "Удалить мои данные", group: "Действия", icon: "trash", keywords: "приватность удалить анкета документ сессия", run: act(() => (onNavigate("check"), setTimeout(() => emit("forget"), 80))) },
      { id: "theme", title: "Сменить тему оформления", group: "Действия", icon: "sun", keywords: "светлая тёмная ночь день", run: act(onTheme) },
      { id: "help", title: "Горячие клавиши", group: "Действия", icon: "keyboard", keywords: "помощь подсказка", run: act(() => emit("help")) },
      ...samples.map<Command>((s) => ({ id: `sample-${s.file}`, title: s.title, hint: s.question, group: "Примеры на столе", icon: "doc", keywords: "пример образец", run: act(() => (onNavigate("check"), setTimeout(() => emit("sample", s), 80))) })),
    ];
  }, [samples, onNavigate, onClose, onTheme]);

  const clauseCommands = useMemo<Command[]>(() => {
    if (!regulation || query.trim().length < 2) return [];
    return rank(regulation.clauses, query, (c) => `${c.number} ${c.text} ${c.section.join(" ")}`, 6).map((c) => ({
      id: `clause-${c.number}`,
      title: `п. ${c.number} — ${c.text.length > 80 ? c.text.slice(0, 77) + "…" : c.text}`,
      group: "Положение",
      icon: "book" as const,
      run: () => (onNavigate("rules", c.number), onClose()),
    }));
  }, [regulation, query, onNavigate, onClose]);

  const items = useMemo(() => [...rank(base, query, (c) => `${c.title} ${c.keywords ?? ""} ${c.hint ?? ""}`), ...clauseCommands], [base, query, clauseCommands]);

  useEffect(() => setActive(0), [query]);
  useEffect(() => {
    list.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") (e.preventDefault(), setActive((a) => Math.min(items.length - 1, a + 1)));
    else if (e.key === "ArrowUp") (e.preventDefault(), setActive((a) => Math.max(0, a - 1)));
    else if (e.key === "Enter") (e.preventDefault(), items[active]?.run());
    else if (e.key === "Escape") (e.preventDefault(), onClose());
    else if (e.key === "Tab") e.preventDefault();
  };

  let lastGroup = "";
  return (
    <div className="palette-wrap" data-open={open} onKeyDown={onKeyDown} inert={!open}>
      <div className="palette-veil" onClick={onClose} aria-hidden="true" />
      <div className="palette" role="dialog" aria-modal="true" aria-label="Поиск и команды">
        <div className="palette-search">
          <Icon name="search" size={18} />
          <input ref={input} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Что сделать? Например: тема, пример, срок рассмотрения…" aria-label="Поиск команд" role="combobox" aria-expanded="true" aria-controls="palette-list" aria-activedescendant={items[active] ? `pal-${items[active]!.id}` : undefined} />
          <kbd>Esc</kbd>
        </div>
        <ul id="palette-list" ref={list} role="listbox" className="palette-list">
          {items.map((c, i) => {
            const head = c.group !== lastGroup ? <li role="presentation" className="pal-group" key={`g-${c.group}-${i}`}>{c.group}</li> : null;
            lastGroup = c.group;
            return [
              head,
              <li key={c.id} id={`pal-${c.id}`} data-idx={i} role="option" aria-selected={i === active} className={i === active ? "active" : ""} onMouseMove={() => setActive(i)} onClick={c.run}>
                <Icon name={c.icon} size={16} />
                <span className="pal-main">
                  <strong>{c.title}</strong>
                  {c.hint && <small>{c.hint}</small>}
                </span>
                {i === active && <Icon name="arrowRight" size={14} />}
              </li>,
            ];
          })}
          {!items.length && <li className="pal-empty" role="presentation">Ничего не найдено. Попробуйте другое слово.</li>}
        </ul>
      </div>
    </div>
  );
}
