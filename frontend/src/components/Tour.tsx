import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { type BusEvent, emit } from "../lib/bus";
import type { SampleItem } from "../types";
import { Icon } from "../ui/Icon";
import { Portal } from "../ui/Portal";
import type { Route } from "./NavBar";

/** Кнопка в карточке подсказки: закрывает подсказки и сразу делает то, о чём шаг (шлёт событие столу). */
export interface TourAction {
  label: string;
  event: BusEvent;
  detail?: unknown;
}

export interface TourStep {
  /** устойчивый ключ шага: цель у соседних шагов может совпадать */
  id: string;
  /** CSS-селектор элемента, на который указывает стрелка */
  target: string;
  title: string;
  text: string;
  actions?: TourAction[];
}

export interface TourContext {
  /** пример для «Попробовать на примере»; нет примеров — нет и кнопки */
  demo?: SampleItem | null;
}

const ask = (text: string): TourAction => ({ label: text, event: "ask", detail: text });
const say = (text: string): TourAction => ({ label: text, event: "say", detail: text });

const emptyDesk = ({ demo }: TourContext): TourStep[] => [
  {
    id: "doc",
    target: ".tray .tray-actions",
    title: "Положите документ",
    text: "Фото, скан или PDF (JPG, PNG). Снимайте целиком, при дневном свете и без бликов — так надёжнее читается.",
    actions: demo ? [{ label: "Попробовать на примере", event: "sample", detail: demo }] : undefined,
  },
  {
    id: "ask",
    target: ".dock-field",
    title: "Спросите по делу",
    text: "Вопрос можно не писать — тогда проверю документ целиком. Без документа отвечу по Положению.",
    actions: [ask("Какие документы нужны для перевода из другого вуза?"), ask("Сколько дней рассматривается заявление?")],
  },
  {
    id: "form",
    target: ".dock-field",
    title: "Нужно заявление?",
    text: "Скажите, что оформляем, — задам несколько вопросов и подготовлю заявление в DOCX.",
    actions: [say("Я хочу перевестись"), say("Хочу поступить")],
  },
  { id: "rules", target: ".nav .switch", title: "Положение под рукой", text: "Все пункты с поиском. Из любого ответа можно открыть пункт, на котором он основан." },
];

/** Что сказать о выводе: зависит от вердикта, а не просто описывает рамку. */
const VERDICT_STEPS: Record<string, Pick<TourStep, "title" | "text" | "actions">> = {
  ok: {
    title: "Ответ и его источник",
    text: "Под ответом — на что он опирается: нажмите на пункт Положения или поле, чтобы прочитать или увидеть на снимке. Нужно заявление — подготовлю здесь же.",
    actions: [say("Я хочу перевестись")],
  },
  missing: {
    title: "Чего не хватает",
    text: "Список — под выводом, у каждого пункта есть ссылка на Положение. Запросите документ с этими данными и положите на стол снова.",
    actions: [{ label: "Положить другой документ", event: "pick-file" }],
  },
  no_answer: {
    title: "Ответа нет",
    text: "Ни в документе, ни в Положении этого не нашлось. Переформулируйте вопрос или спросите в деканате.",
    actions: [{ label: "Задать другой вопрос", event: "focus-question" }],
  },
};

const deskWithDocument = (root: ParentNode): TourStep[] => {
  const verdict = VERDICT_STEPS[root.querySelector(".verdict")?.getAttribute("data-verdict") ?? ""] ?? VERDICT_STEPS.no_answer!;
  const editable = !!root.querySelector(".f-fix");
  return [
    { id: "verdict", target: ".verdict", ...verdict },
    {
      id: "fields",
      target: ".fields",
      title: "Проверьте поля",
      text: "Нажмите на поле — оно подсветится на снимке." + (editable ? " Прочитано неверно? Наведите на поле и нажмите карандаш — ответ пересчитается." : ""),
    },
    { id: "viewer", target: ".viewer", title: "Снимок", text: "Колесо мыши — масштаб, перетаскивание — сдвиг. «Текст» — распознанный текст: его можно найти, скопировать и скачать." },
    {
      id: "followup",
      target: ".dock-field",
      title: "Уточните по этому документу",
      text: "Спросите о нём ещё — отвечу по тому же снимку.",
      actions: [ask("Хватит ли этого документа для перевода?"), ask("По Положению: сколько дней рассматривается заявление?")],
    },
  ];
};

const RULES: TourStep[] = [
  { id: "search", target: ".reg .search", title: "Найдите пункт", text: "Введите слово или номер: «перевод», «справка», «3.4»." },
  { id: "grid", target: ".reg-grid", title: "Откуда берутся ответы", text: "Секретарь отвечает по этим пунктам и называет номер. Загляните сюда, чтобы проверить формулировку самому." },
];

const STATUS: TourStep[] = [
  { id: "status", target: ".status-form", title: "Что с обращением", text: "Введите номер и код, которые показали при отправке, — увидите этап и ответ деканата. Входить не нужно." },
];

const accountSteps = (root: ParentNode): TourStep[] =>
  root.querySelector(".acc-card")
    ? [{ id: "notify", target: ".acc-card", title: "Ответы придут в Telegram", text: "Включите уведомления — ответ деканата по вашему обращению придёт в чат. Анкета и документы общие для сайта и бота." }]
    : [{ id: "login", target: ".account-view .btn.primary", title: "Войдите через Telegram", text: "Необязательно, но тогда анкета и документы общие для сайта и бота, а ответ деканата придёт в чат." }];

const BACK_TO_DESK: TourStep = { id: "sections", target: ".nav .switch", title: "Вернуться к делу", text: "«Стол» — проверка документа и анкета, «Положение» — правила." };

/** Шаги для текущего экрана — только основное; у стола с документом, пустого стола и других страниц они свои. */
export function tourSteps(route: Route, root: ParentNode = document, ctx: TourContext = {}): TourStep[] {
  if (route === "check") return root.querySelector(".viewer") ? deskWithDocument(root) : emptyDesk(ctx);
  if (route === "rules") return RULES;
  return [...(route === "status" ? STATUS : accountSteps(root)), BACK_TO_DESK];
}

// ------------------------------------------------------------------ геометрия
export interface Box {
  left: number;
  top: number;
  width: number;
  height: number;
}
export type Side = "top" | "bottom" | "left" | "right";
export interface Placement {
  side: Side;
  left: number;
  top: number;
  /** стрелка от подсказки к элементу: S-образная кривая и наконечник */
  path: string;
  head: string;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/** Где поставить подсказку и как провести стрелку: ниже элемента, иначе выше, иначе сбоку — где больше места. */
export function placeBubble(target: Box, bubble: { w: number; h: number }, view: { w: number; h: number }, gap = 54, margin = 16): Placement {
  const right = target.left + target.width;
  const bottom = target.top + target.height;
  const cx = target.left + target.width / 2;
  const cy = target.top + target.height / 2;
  const room: Record<Side, number> = { bottom: view.h - bottom, top: target.top, right: view.w - right, left: target.left };
  const need: Record<Side, number> = { bottom: bubble.h + gap + margin, top: bubble.h + gap + margin, right: bubble.w + gap + margin, left: bubble.w + gap + margin };
  const order: Side[] = ["bottom", "top", "right", "left"];
  const side = order.find((s) => room[s] >= need[s]) ?? order.slice().sort((a, b) => room[b] / need[b] - room[a] / need[a])[0]!;

  let left: number;
  let top: number;
  if (side === "bottom" || side === "top") {
    left = clamp(cx - bubble.w / 2, margin, Math.max(margin, view.w - bubble.w - margin));
    top = side === "bottom" ? bottom + gap : target.top - gap - bubble.h;
  } else {
    top = clamp(cy - bubble.h / 2, margin, Math.max(margin, view.h - bubble.h - margin));
    left = side === "right" ? right + gap : target.left - gap - bubble.w;
  }
  top = clamp(top, margin, Math.max(margin, view.h - bubble.h - margin));
  left = clamp(left, margin, Math.max(margin, view.w - bubble.w - margin));

  let x1: number, y1: number, x2: number, y2: number, dx: number, dy: number;
  if (side === "bottom" || side === "top") {
    x1 = clamp(cx, left + 28, left + bubble.w - 28);
    x2 = clamp(cx, target.left + 10, right - 10);
    y1 = side === "bottom" ? top : top + bubble.h;
    y2 = side === "bottom" ? bottom + 4 : target.top - 4;
    dx = 0;
    dy = y2 < y1 ? -1 : 1;
  } else {
    y1 = clamp(cy, top + 28, top + bubble.h - 28);
    y2 = clamp(cy, target.top + 10, bottom - 10);
    x1 = side === "right" ? left : left + bubble.w;
    x2 = side === "right" ? right + 4 : target.left - 4;
    dx = x2 < x1 ? -1 : 1;
    dy = 0;
  }
  const f = (n: number) => n.toFixed(1);
  const vertical = dx === 0;
  const path = vertical
    ? `M${f(x1)} ${f(y1)} C${f(x1)} ${f((y1 + y2) / 2)} ${f(x2)} ${f((y1 + y2) / 2)} ${f(x2)} ${f(y2 - dy * 9)}`
    : `M${f(x1)} ${f(y1)} C${f((x1 + x2) / 2)} ${f(y1)} ${f((x1 + x2) / 2)} ${f(y2)} ${f(x2 - dx * 9)} ${f(y2)}`;
  // наконечник: треугольник с вершиной в точке (x2, y2), смотрит от подсказки к элементу
  const head = vertical
    ? `M${f(x2)} ${f(y2)} L${f(x2 - 7)} ${f(y2 - dy * 12)} L${f(x2 + 7)} ${f(y2 - dy * 12)}Z`
    : `M${f(x2)} ${f(y2)} L${f(x2 - dx * 12)} ${f(y2 - 7)} L${f(x2 - dx * 12)} ${f(y2 + 7)}Z`;
  return { side, left, top, path, head };
}

const shown = (el: Element): boolean => {
  const r = (el as HTMLElement).getClientRects?.();
  return !!r && r.length > 0;
};

const same = (a: Box, b: Box) => Math.abs(a.left - b.left) < 0.5 && Math.abs(a.top - b.top) < 0.5 && Math.abs(a.width - b.width) < 0.5 && Math.abs(a.height - b.height) < 0.5;
const PAD = 8;
const BUBBLE_W = 340;

interface Props {
  open: boolean;
  route: Route;
  onClose: () => void;
  /** пример для кнопки «Попробовать на примере» */
  demo?: SampleItem | null;
  /** для тестов: в jsdom у элементов нет геометрии */
  isVisible?: (el: Element) => boolean;
}

/**
 * Пошаговые подсказки: затемнение, «окно» вокруг нужного элемента, поясняющая карточка и стрелка от неё к элементу.
 * Стрелки ←/→ листают, Esc закрывает. Показываются только те шаги, чьи элементы сейчас есть на экране.
 */
export function Tour({ open, route, onClose, demo = null, isVisible = shown }: Props) {
  const [steps, setSteps] = useState<TourStep[]>([]);
  const [index, setIndex] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [size, setSize] = useState({ w: BUBBLE_W, h: 190 });
  const [view, setView] = useState({ w: typeof innerWidth === "number" ? innerWidth : 1280, h: typeof innerHeight === "number" ? innerHeight : 720 });
  const card = useRef<HTMLDivElement>(null);
  const nextBtn = useRef<HTMLButtonElement>(null);
  const step = steps[index];
  const last = index === steps.length - 1;

  // при открытии собираем шаги, у которых есть видимая цель
  useEffect(() => {
    if (!open) return;
    setSteps(tourSteps(route, document, { demo }).filter((s) => {
      const el = document.querySelector(s.target);
      return !!el && isVisible(el);
    }));
    setIndex(0);
    setBox(null);
  }, [open, route, demo, isVisible]);

  // следим за рамкой цели: страница может прокручиваться и менять размер, пока идёт тур
  useEffect(() => {
    if (!open || !step) return;
    const el = document.querySelector(step.target) as HTMLElement | null;
    if (!el) return;
    const reduced = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
    // панель сверху закреплена: к ней просто возвращаемся наверх, а остальное прокручиваем ровно настолько, чтобы элемент был виден
    if (el.closest(".nav")) window.scrollTo?.({ top: 0, behavior: reduced ? "auto" : "smooth" });
    else el.scrollIntoView?.({ block: "nearest", inline: "nearest", behavior: reduced ? "auto" : "smooth" });
    let raf = 0;
    const tick = () => {
      const r = el.getBoundingClientRect();
      const next = { left: r.left - PAD, top: r.top - PAD, width: r.width + PAD * 2, height: r.height + PAD * 2 };
      setBox((prev) => (prev && same(prev, next) ? prev : next));
      setView((v) => (v.w === innerWidth && v.h === innerHeight ? v : { w: innerWidth, h: innerHeight }));
      raf = requestAnimationFrame(tick);
    };
    tick();
    return () => cancelAnimationFrame(raf);
  }, [open, step]);

  // пока идут подсказки, панель не прячется при прокрутке: на неё тоже показывают стрелкой
  useEffect(() => {
    if (!open) return;
    document.documentElement.classList.add("tour-open");
    return () => document.documentElement.classList.remove("tour-open");
  }, [open]);

  // размер карточки нужен, чтобы выбрать сторону и провести стрелку
  useLayoutEffect(() => {
    const r = card.current?.getBoundingClientRect();
    if (r && r.width > 0 && (Math.abs(r.width - size.w) > 1 || Math.abs(r.height - size.h) > 1)) setSize({ w: r.width, h: r.height });
  });

  useEffect(() => {
    if (open && step) nextBtn.current?.focus({ preventScroll: true });
  }, [open, index, step]);

  const go = useCallback((d: number) => setIndex((i) => clamp(i + d, 0, Math.max(0, steps.length - 1))), [steps.length]);
  const next = () => (last ? onClose() : go(1));
  const run = (a: TourAction) => {
    onClose();
    emit(a.event, a.detail);
  };

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") (e.preventDefault(), onClose());
      else if (e.key === "ArrowRight") (e.preventDefault(), go(1));
      else if (e.key === "ArrowLeft") (e.preventDefault(), go(-1));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, go, onClose]);

  if (!open) return null;
  if (!step) {
    return (
      <Portal>
        <div className="tour" role="dialog" aria-modal="true" aria-label="Подсказки">
          <div className="tour-shield" onClick={onClose} />
          <div className="tour-card tour-empty" ref={card} style={{ left: "50%", top: "40%" }}>
            <p>Здесь пока нечего показывать. Откройте стол и нажмите «?» ещё раз.</p>
            <button type="button" className="btn primary small" onClick={onClose}>
              Понятно
            </button>
          </div>
        </div>
      </Portal>
    );
  }
  const place = box ? placeBubble(box, { w: Math.min(size.w, view.w - 32), h: size.h }, view) : null;

  return (
    <Portal>
      <div className="tour" role="dialog" aria-modal="true" aria-labelledby="tour-title" aria-describedby="tour-text">
        <div className="tour-shield" />
        {box && <div className="tour-spot" style={{ left: box.left, top: box.top, width: box.width, height: box.height }} />}
        {place && (
          <svg className="tour-arrow" key={index} width="100%" height="100%" aria-hidden="true">
            <path d={place.path} className="tour-arrow-line" />
            <path d={place.head} className="tour-arrow-head" />
          </svg>
        )}
        <div className="tour-card" ref={card} data-side={place?.side} style={place ? { left: place.left, top: place.top, width: Math.min(BUBBLE_W, view.w - 32) } : { left: -9999, top: 0, width: BUBBLE_W }} key={`c${index}`}>
          <p className="eyebrow tabnum">
            Подсказка {index + 1} из {steps.length}
          </p>
          <h2 id="tour-title">{step.title}</h2>
          <p id="tour-text" className="tour-text">
            {step.text}
          </p>
          {step.actions && step.actions.length > 0 && (
            <div className="tour-actions" role="group" aria-label="Попробовать сразу">
              {step.actions.map((a) => (
                <button key={a.label} type="button" className="tour-chip" onClick={() => run(a)}>
                  {a.label}
                  <Icon name="arrowRight" size={13} />
                </button>
              ))}
            </div>
          )}
          <div className="tour-foot">
            <button type="button" className="link tour-skip" onClick={onClose}>
              {last ? "Закрыть" : "Пропустить"}
            </button>
            <span className="grow" />
            <button type="button" className="btn small" onClick={() => go(-1)} disabled={index === 0}>
              <Icon name="arrowRight" size={14} className="tour-back" /> Назад
            </button>
            <button type="button" className="btn primary small" ref={nextBtn} onClick={next}>
              {last ? "Готово" : "Далее"}
              {!last && <Icon name="arrowRight" size={14} />}
            </button>
          </div>
          <div className="tour-dots" aria-hidden="true">
            {steps.map((s, k) => (
              <i key={s.id} className={k === index ? "on" : k < index ? "done" : ""} />
            ))}
          </div>
        </div>
      </div>
    </Portal>
  );
}
