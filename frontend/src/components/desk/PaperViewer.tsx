import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { animate, lerp, useReducedMotion } from "../../lib/motion";
import { formatBytes, unionBoxes } from "../../lib/format";
import type { Box, PagePreview } from "../../types";
import { renderAnnotated } from "../../lib/annotate";
import { Icon } from "../../ui/Icon";
import { useToast } from "../../ui/Toasts";
import { BoxMarks } from "./BoxMarks";
import { DocStrip } from "./DocStrip";
import { OcrTextPanel } from "./OcrTextPanel";
import type { DeskDoc, Highlight } from "./highlight";

interface Props {
  /** адрес картинки: локальный предпросмотр или готовый снимок из ответа */
  src: string | null;
  /** размер файла в байтах: показывается в строке под листом */
  fileSize?: number;
  /** распознанный текст документа: открывается кнопкой «Текст» под снимком */
  text?: string;
  /** показать фразу из текста на снимке (уходит вопросом «Где на документе «…»?») */
  onFindText?: (phrase: string) => void;
  /** размеры страницы, к которой относится src, если картинка меньше неё (предпросмотр PDF): лист сразу ложится в нужный размер */
  srcSize?: { w: number; h: number } | null;
  pages: PagePreview[] | null;
  fileName: string;
  pdf: boolean;
  scanning: boolean;
  progress: number;
  stageLabel: string;
  highlight: Highlight | null;
  hover: Highlight | null;
  allBoxes: Box[];
  showAll: boolean;
  /** меняется, когда приходит новый результат: рамки «проявляются» заново */
  revealKey: string;
  onToggleAll: () => void;
  onClear: () => void;
  docs: DeskDoc[];
  activeDoc: number;
  busy: boolean;
  onSelectDoc: (index: number) => void;
  onAnother: () => void;
  /** крупная кнопка над панелью инструментов: «Проверить этот документ» для только что положенного файла */
  cta?: { label: string; onClick: () => void } | null;
}

interface View {
  s: number;
  tx: number;
  ty: number;
}

const MAX_ZOOM = 9;

export function PaperViewer(p: Props) {
  const reduced = useReducedMotion();
  const toast = useToast();
  const imgRef = useRef<HTMLImageElement>(null);
  const [enhance, setEnhance] = useState(false);
  const [showText, setShowText] = useState(false);
  const stage = useRef<HTMLDivElement>(null);
  const plane = useRef<HTMLDivElement>(null);
  const [page, setPage] = useState(0);
  const [size, setSize] = useState({ w: 640, h: 560 });
  const [natural, setNatural] = useState<{ w: number; h: number } | null>(null);
  // лист «проявляется» один раз — когда на стол кладут документ. Смена картинки того же документа (предпросмотр -> страница из ответа)
  // анимацию не повторяет, а картинка, пришедшая на место заглушки PDF, ложится без проявления: лист не мигает
  const placeholderFor = useRef("");
  const [view, setView] = useState<View>({ s: 1, tx: 0, ty: 0 });
  const viewRef = useRef(view);
  viewRef.current = view;
  const [dragging, setDragging] = useState(false);
  const stopFly = useRef<(() => void) | null>(null);
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const gesture = useRef<{ view: View; x: number; y: number; dist: number } | null>(null);

  const pageInfo = p.pages?.[Math.min(page, (p.pages?.length ?? 1) - 1)] ?? null;
  const src = pageInfo?.data_url ?? p.src;
  const iw = pageInfo?.width ?? p.srcSize?.w ?? natural?.w ?? 1000;
  const ih = pageInfo?.height ?? p.srcSize?.h ?? natural?.h ?? 1414;
  const fit = Math.min(size.w / iw, size.h / ih) * 0.94;
  const fitView = useCallback((): View => ({ s: fit, tx: (size.w - iw * fit) / 2, ty: (size.h - ih * fit) / 2 }), [fit, size.w, size.h, iw, ih]);

  const fly = useCallback(
    (to: View, ms = 640) => {
      stopFly.current?.();
      if (reduced) return setView(to);
      const from = viewRef.current;
      stopFly.current = animate(ms, (t) => setView({ s: from.s * Math.pow(to.s / from.s, t), tx: lerp(from.tx, to.tx, t), ty: lerp(from.ty, to.ty, t) }));
    },
    [reduced],
  );

  useLayoutEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => setSize({ w: Math.max(240, el.clientWidth), h: Math.max(260, el.clientHeight) });
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // другой документ — панель текста закрывается: в ней был текст прежнего
  useEffect(() => setShowText(false), [p.fileName]);

  // новая страница, снимок или размер — вписываем лист
  useEffect(() => {
    stopFly.current?.();
    setView(fitView());
  }, [fitView, src]);

  // выбранный фрагмент — камера плавно подлетает к нему
  useEffect(() => {
    if (!p.highlight) return;
    const onPage = p.highlight.boxes.filter((b) => b.page === page);
    if (!onPage.length && p.highlight.boxes.length) {
      setPage(p.highlight.boxes[0]!.page);
      return;
    }
    const u = unionBoxes(onPage);
    if (!u) return;
    const bw = Math.max(60, u.x1 - u.x0);
    const bh = Math.max(30, u.y1 - u.y0);
    const s = Math.min(Math.max(fit, Math.min((size.w * 0.72) / bw, (size.h * 0.5) / bh)), fit * 4.2, fit * MAX_ZOOM);
    fly({ s, tx: size.w / 2 - ((u.x0 + u.x1) / 2) * s, ty: size.h / 2 - ((u.y0 + u.y1) / 2) * s });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [p.highlight?.id, page]);

  const zoomAt = useCallback(
    (cx: number, cy: number, factor: number) => {
      stopFly.current?.();
      setView((v) => {
        const s = Math.min(Math.max(v.s * factor, fit * 0.5), fit * MAX_ZOOM);
        const k = s / v.s;
        return { s, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k };
      });
    },
    [fit],
  );

  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      zoomAt(e.clientX - r.left, e.clientY - r.top, Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016)));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [zoomAt]);

  // лёгкий наклон «стола» вслед за курсором; на сенсорных экранах и при reduced-motion выключен
  const onHover = (e: React.PointerEvent) => {
    if (reduced || e.pointerType !== "mouse" || pointers.current.size) return;
    const r = e.currentTarget.getBoundingClientRect();
    const nx = ((e.clientX - r.left) / r.width) * 2 - 1;
    const ny = ((e.clientY - r.top) / r.height) * 2 - 1;
    plane.current?.style.setProperty("--ry", `${(nx * 1.6).toFixed(2)}deg`);
    plane.current?.style.setProperty("--rx", `${(-ny * 1.2).toFixed(2)}deg`);
  };
  const resetTilt = () => {
    plane.current?.style.setProperty("--ry", "0deg");
    plane.current?.style.setProperty("--rx", "0deg");
  };

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button !== 0) return;
    stopFly.current?.();
    (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const pts = [...pointers.current.values()];
    gesture.current = { view: viewRef.current, x: pts[0]!.x, y: pts[0]!.y, dist: pts.length > 1 ? Math.hypot(pts[0]!.x - pts[1]!.x, pts[0]!.y - pts[1]!.y) : 0 };
    setDragging(true);
    resetTilt();
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!pointers.current.has(e.pointerId)) return onHover(e);
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    const g = gesture.current;
    if (!g) return;
    const pts = [...pointers.current.values()];
    if (pts.length >= 2 && g.dist > 0) {
      const r = stage.current!.getBoundingClientRect();
      const cx = (pts[0]!.x + pts[1]!.x) / 2 - r.left;
      const cy = (pts[0]!.y + pts[1]!.y) / 2 - r.top;
      const k = Math.hypot(pts[0]!.x - pts[1]!.x, pts[0]!.y - pts[1]!.y) / g.dist;
      const s = Math.min(Math.max(g.view.s * k, fit * 0.5), fit * MAX_ZOOM);
      const kk = s / g.view.s;
      setView({ s, tx: cx - (cx - g.view.tx) * kk, ty: cy - (cy - g.view.ty) * kk });
    } else {
      setView({ ...g.view, tx: g.view.tx + e.clientX - g.x, ty: g.view.ty + e.clientY - g.y });
    }
  };
  const endPointer = (e: React.PointerEvent) => {
    pointers.current.delete(e.pointerId);
    gesture.current = null;
    if (pointers.current.size === 1) {
      const rest = [...pointers.current.values()][0]!;
      gesture.current = { view: viewRef.current, x: rest.x, y: rest.y, dist: 0 };
    }
    if (!pointers.current.size) setDragging(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    const c = { x: size.w / 2, y: size.h / 2 };
    const pan = (dx: number, dy: number) => (stopFly.current?.(), setView((v) => ({ ...v, tx: v.tx + dx, ty: v.ty + dy })));
    const keys: Record<string, () => void> = {
      PageDown: () => setPage((n) => Math.min(n + 1, (p.pages?.length ?? 1) - 1)),
      PageUp: () => setPage((n) => Math.max(n - 1, 0)),
      "+": () => zoomAt(c.x, c.y, 1.25),
      "=": () => zoomAt(c.x, c.y, 1.25),
      "-": () => zoomAt(c.x, c.y, 0.8),
      "0": () => fly(fitView()),
      ArrowLeft: () => pan(60, 0),
      ArrowRight: () => pan(-60, 0),
      ArrowUp: () => pan(0, 60),
      ArrowDown: () => pan(0, -60),
    };
    const fn = keys[e.key];
    if (fn) {
      e.preventDefault();
      fn();
    }
  };

  const save = () => {
    const el = imgRef.current;
    if (!el || !el.complete || !el.naturalWidth) return toast.show("Снимок ещё не загрузился", "info");
    try {
      const canvas = renderAnnotated(el, iw, ih, page, p.showAll ? p.allBoxes : [], p.highlight?.boxes ?? []);
      canvas.toBlob((blob) => {
        if (!blob) return toast.show("Не удалось сохранить снимок", "error");
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = `${(p.fileName || "документ").replace(/\.[^.]+$/, "")}-подсветка.png`;
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1500);
        toast.show("Снимок с подсветкой сохранён", "ok");
      }, "image/png");
    } catch {
      toast.show("Не удалось сохранить снимок", "error");
    }
  };

  const zoomed = view.s > fit * 1.35;
  const hl = p.highlight?.boxes.filter((b) => b.page === page) ?? [];
  const hv = p.hover?.boxes.filter((b) => b.page === page) ?? [];
  const dim = p.showAll ? p.allBoxes.filter((b) => b.page === page) : [];
  const zoomPct = Math.round((view.s / fit) * 100);

  const jump = (e: React.MouseEvent) => {
    const r = e.currentTarget.getBoundingClientRect();
    const ix = ((e.clientX - r.left) / r.width) * iw;
    const iy = ((e.clientY - r.top) / r.height) * ih;
    fly({ s: viewRef.current.s, tx: size.w / 2 - ix * viewRef.current.s, ty: size.h / 2 - iy * viewRef.current.s }, 380);
  };

  return (
    <section className={`viewer ${p.scanning ? "is-scanning" : ""}`} aria-label="Снимок документа">
      <div className="viewer-top">
        <DocStrip docs={p.docs} active={p.activeDoc} disabled={p.busy} onSelect={p.onSelectDoc} onAnother={p.onAnother} />
        {p.highlight && (
          <button type="button" className="chip-glass hl-chip" onClick={p.onClear} title="Убрать подсветку">
            <i aria-hidden="true" />
            {p.highlight.label}
            <Icon name="x" size={12} />
          </button>
        )}
      </div>

      <div
        ref={stage}
        className={`stage ${dragging ? "grabbing" : ""}`}
        tabIndex={0}
        role="application"
        aria-label="Снимок документа. Колесо мыши — масштаб, перетаскивание — сдвиг; клавиши плюс, минус, ноль и стрелки."
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={endPointer}
        onPointerCancel={endPointer}
        onPointerLeave={resetTilt}
        onKeyDown={onKeyDown}
        onDoubleClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          if (zoomed) fly(fitView());
          else {
            const k = 2.2;
            const cx = e.clientX - r.left;
            const cy = e.clientY - r.top;
            const v = viewRef.current;
            fly({ s: v.s * k, tx: cx - (cx - v.tx) * k, ty: cy - (cy - v.ty) * k }, 420);
          }
        }}
      >
        <div ref={plane} className="plane">
          <div className="canvas" style={{ width: iw, height: ih, transform: `translate(${view.tx}px, ${view.ty}px) scale(${view.s})`, ["--inv" as string]: 1 / view.s }}>
            {src ? (
              <img
                key={p.fileName}
                ref={imgRef}
                className={placeholderFor.current === p.fileName ? "settled" : undefined}
                style={enhance ? { filter: "contrast(1.55) brightness(1.06) grayscale(1)" } : undefined}
                src={src}
                alt=""
                draggable={false}
                width={iw}
                height={ih}
                onLoad={(e) => !pageInfo && setNatural({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
              />
            ) : (
              <div className="pdf-sheet" aria-hidden="true" ref={() => void (placeholderFor.current = p.fileName)}>
                <Icon name="doc" size={96} />
                <strong>{p.pdf ? "PDF" : "Документ"}</strong>
                <span>{p.fileName}</span>
              </div>
            )}
            <BoxMarks boxes={dim} kind="dim" keyPrefix={`${p.revealKey}d`} width={iw} height={ih} />
            <BoxMarks boxes={hv} kind="hover" keyPrefix={`hv${p.hover?.id}`} width={iw} height={ih} />
            <BoxMarks boxes={hl} kind="hit" keyPrefix={`${p.highlight?.id}`} width={iw} height={ih} />
            {p.scanning && <span className="beam" aria-hidden="true" />}
          </div>
        </div>
        {p.scanning && (
          <>
            <span className="corner tl" aria-hidden="true" />
            <span className="corner tr" aria-hidden="true" />
            <span className="corner bl" aria-hidden="true" />
            <span className="corner br" aria-hidden="true" />
            <div className="scan-badge" role="status">
              <span className="tabnum">{Math.round(p.progress * 100)}%</span>
              <span>{p.stageLabel}</span>
            </div>
          </>
        )}

        {zoomed && src && (
          <button type="button" className="minimap" onClick={jump} aria-label="Миникарта: перейти к месту на листе" style={{ aspectRatio: `${iw} / ${ih}` }}>
            <img src={src} alt="" draggable={false} />
            <span
              className="mm-view"
              style={{
                left: `${Math.max(0, (-view.tx / view.s / iw) * 100)}%`,
                top: `${Math.max(0, (-view.ty / view.s / ih) * 100)}%`,
                width: `${Math.min(100, (size.w / view.s / iw) * 100)}%`,
                height: `${Math.min(100, (size.h / view.s / ih) * 100)}%`,
              }}
            />
          </button>
        )}
      </div>

      {p.cta && (
        <button type="button" className="viewer-cta" onClick={p.cta.onClick} disabled={p.busy}>
          <Icon name="arrowRight" size={16} /> {p.cta.label}
        </button>
      )}

      {showText && p.text && (
        <OcrTextPanel
          text={p.text}
          fileName={p.fileName}
          busy={p.busy}
          onFind={
            p.onFindText
              ? (phrase) => {
                  setShowText(false); // панель закрывается, чтобы найденное место на снимке было видно
                  p.onFindText?.(phrase);
                }
              : undefined
          }
          onClose={() => setShowText(false)}
        />
      )}

      <p className="viewer-meta tabnum" aria-label="Сведения о листе">
        {[`${iw} × ${ih} px`, p.fileSize ? formatBytes(p.fileSize) : "", p.pages && p.pages.length > 1 ? `стр. ${page + 1} из ${p.pages.length}` : ""].filter(Boolean).join(" · ")}
      </p>

      <div className="viewer-tools" role="toolbar" aria-label="Управление снимком">
        {p.pages && p.pages.length > 1 && (
          <div className="pager" role="group" aria-label="Страницы">
            {p.pages.map((im, i) => (
              <button key={im.page} type="button" className={i === page ? "on" : ""} aria-pressed={i === page} title={`Страница ${i + 1} (PageUp / PageDown)`} onClick={() => setPage(i)}>
                {i + 1}
              </button>
            ))}
          </div>
        )}
        <button type="button" className="tool" aria-label="Уменьшить" title="Уменьшить (−)" onClick={() => zoomAt(size.w / 2, size.h / 2, 0.78)}>
          <Icon name="zoomOut" />
        </button>
        <span className="zoom tabnum" aria-live="off">
          {zoomPct}%
        </span>
        <button type="button" className="tool" aria-label="Увеличить" title="Увеличить (+)" onClick={() => zoomAt(size.w / 2, size.h / 2, 1.28)}>
          <Icon name="zoomIn" />
        </button>
        <button type="button" className="tool" aria-label="Вписать лист" title="Вписать лист (0)" onClick={() => fly(fitView())}>
          <Icon name="fit" />
        </button>
        {p.allBoxes.length > 0 && (
          <button type="button" className={`tool wide ${p.showAll ? "on" : ""}`} aria-pressed={p.showAll} onClick={p.onToggleAll} title="Показать рамки всех найденных полей">
            <Icon name="eye" />
            <span>Все поля</span>
          </button>
        )}
        {p.text && (
          <button type="button" className={`tool wide ${showText ? "on" : ""}`} aria-pressed={showText} onClick={() => setShowText((v) => !v)} title="Распознанный текст: прочитать, найти, скопировать, скачать">
            <Icon name="doc" />
            <span>Текст</span>
          </button>
        )}
        <button type="button" className={`tool wide ${enhance ? "on" : ""}`} aria-pressed={enhance} onClick={() => setEnhance((v) => !v)} title="Повысить контраст: поможет прочитать бледный или тёмный снимок">
          <Icon name="contrast" />
          <span>Контраст</span>
        </button>
        {src && (
          <button type="button" className="tool" onClick={save} aria-label="Сохранить снимок с подсветкой" title="Сохранить снимок с подсветкой (PNG)">
            <Icon name="download" />
          </button>
        )}
      </div>
    </section>
  );
}
