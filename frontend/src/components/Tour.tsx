import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "../ui/Icon";
import { Portal } from "../ui/Portal";
import type { Route } from "./NavBar";

export interface TourStep {
  /** CSS-селектор элемента, на который указывает стрелка */
  target: string;
  title: string;
  text: string;
}

const DESK_EMPTY: TourStep[] = [
  { target: ".tray .tray-actions", title: "Сюда кладём документ", text: "Нажмите «Выбрать файл», перетащите снимок на стол или вставьте его из буфера (Ctrl+V). Подойдут JPG, PNG и PDF." },
  { target: ".dock-field", title: "Вопрос", text: "Напишите, что хотите узнать, например «Чего не хватает в документе?». Вопрос можно не писать: тогда я проверю документ целиком." },
  { target: ".dock-bar .round-btn", title: "Скрепка", text: "Ещё один способ приложить документ: выбрать файл на устройстве." },
  { target: ".picker", title: "Тип документа", text: "Тип определяется сам. Если я ошибся, выберите нужный вручную: справка об обучении, заявление на академический отпуск, справка о практике или «другой документ»." },
  { target: ".dock .send", title: "Проверить", text: "Отправляет документ и вопрос. То же делает клавиша Enter." },
  { target: ".starts", title: "Не знаете, с чего начать?", text: "Нажмите «Что ты умеешь?» — расскажу, что умею. Ниже — готовые вопросы и действия: анкета для заявления, вопросы по Положению, проверка документа." },
];

const DESK_DOC: TourStep[] = [
  { target: ".viewer", title: "Ваш документ", text: "Колесо мыши — масштаб, перетаскивание — сдвиг, двойной щелчок — приблизить место. Найденные поля подсвечиваются рамками." },
  { target: ".viewer-tools", title: "Панель снимка", text: "Масштаб, «Все поля» — рамки всех найденных полей, «Текст» — распознанный текст, «Контраст» — для бледных и тёмных снимков." },
  { target: ".verdict", title: "Заключение", text: "Один из трёх выводов: документ в порядке, чего-то не хватает или ответа нет. Под ним — пункт Положения, на котором он основан: нажмите на него, чтобы прочитать." },
  { target: ".fields", title: "Карточка полей", text: "Нажмите на поле — покажу это место на снимке. Прочитано неверно? Нажмите на карандаш и впишите значение: ответ пересчитается." },
  { target: ".followups", title: "Спросить дальше", text: "Готовые уточняющие вопросы по этому документу — нажмите, и я отвечу." },
  { target: ".dock-field", title: "Ещё вопрос", text: "Можно задать любой вопрос по этому же документу или по Положению. Фразы «покажи РФ» или «где дата» отметят место на снимке." },
];

const RULES: TourStep[] = [
  { target: ".reg .search", title: "Поиск по Положению", text: "Введите слово или номер пункта — найду нужное место. Совпадения подсвечиваются." },
  { target: ".reg-grid", title: "Пункты Положения", text: "Пункты сгруппированы по разделам. На них ссылаются ответы секретаря: нажмите на «п. 3.4» в ответе, и откроется нужный пункт." },
];

const NAV: TourStep[] = [
  { target: ".nav .switch", title: "Разделы", text: "«Стол» — проверка документа и анкета для заявления. «Положение» — все пункты правил с поиском." },
  { target: ".nav .tools .tour-btn", title: "Эта кнопка", text: "Подсказки можно открыть снова в любой момент — на любой странице." },
  { target: ".nav .tools .round-btn:not(.tour-btn)", title: "Поиск и команды", text: "Ctrl+K открывает строку: переходы, примеры, действия и поиск по пунктам Положения." },
  { target: ".nav-menu .menu-btn", title: "Меню", text: "Тема оформления, горячие клавиши, статус обращения, вход для сотрудников и «Удалить мои данные»." },
];

/** Шаги для текущего экрана: у стола с документом они свои, у пустого стола — свои, потом общая панель. */
export function tourSteps(route: Route, root: ParentNode = document): TourStep[] {
  const screen = route === "check" ? (root.querySelector(".viewer") ? DESK_DOC : DESK_EMPTY) : route === "rules" ? RULES : [];
  return [...screen, ...NAV];
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
  /** для тестов: в jsdom у элементов нет геометрии */
  isVisible?: (el: Element) => boolean;
}

/**
 * Пошаговые подсказки: затемнение, «окно» вокруг нужного элемента, поясняющая карточка и стрелка от неё к элементу.
 * Стрелки ←/→ листают, Esc закрывает. Показываются только те шаги, чьи элементы сейчас есть на экране.
 */
export function Tour({ open, route, onClose, isVisible = shown }: Props) {
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
    setSteps(tourSteps(route).filter((s) => {
      const el = document.querySelector(s.target);
      return !!el && isVisible(el);
    }));
    setIndex(0);
    setBox(null);
  }, [open, route, isVisible]);

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
              <i key={s.target} className={k === index ? "on" : k < index ? "done" : ""} />
            ))}
          </div>
        </div>
      </div>
    </Portal>
  );
}
