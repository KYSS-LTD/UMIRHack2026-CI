import { Fragment, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { loadRegulation, useRegulation } from "../hooks/useRegulation";
import type { Clause } from "../types";
import { Icon } from "../ui/Icon";
import { clauseNo, plural } from "../lib/format";
import { linkRefs } from "../lib/refs";
import { useToast } from "../ui/Toasts";

/** Подсвечивает совпадения запроса в тексте пункта. */
export function highlightText(text: string, query: string): (string | { hit: string })[] {
  const q = query.trim();
  if (q.length < 2) return [text];
  const out: (string | { hit: string })[] = [];
  const lower = text.toLowerCase();
  const needle = q.toLowerCase();
  let i = 0;
  for (;;) {
    const j = lower.indexOf(needle, i);
    if (j < 0) break;
    if (j > i) out.push(text.slice(i, j));
    out.push({ hit: text.slice(j, j + needle.length) });
    i = j + needle.length;
  }
  out.push(text.slice(i));
  return out;
}

export function filterClauses(clauses: Clause[], query: string): Clause[] {
  const q = query.trim().toLowerCase();
  if (!q) return clauses;
  return clauses.filter((c) => c.text.toLowerCase().includes(q) || clauseNo(c).includes(q) || (c.document?.name ?? "").toLowerCase().includes(q) || c.section.join(" ").toLowerCase().includes(q));
}

export interface Section {
  id: string;
  title: string;
  clauses: Clause[];
}

/** Группировка пунктов по разделам в исходном порядке: из неё строится оглавление. Документ базы знаний — отдельный раздел со своим названием. */
export function groupSections(clauses: Clause[]): Section[] {
  const out: Section[] = [];
  for (const c of clauses) {
    const title = c.document ? `Документ «${c.document.name}»` : (c.section[0] ?? "Общие положения");
    let s = out[out.length - 1];
    if (!s || s.title !== title) {
      s = { id: `sec-${out.length}`, title, clauses: [] };
      out.push(s);
    }
    s.clauses.push(c);
  }
  return out;
}

/** «polozhenie.md · пунктов: 25 · и ещё 2 документа базы знаний» — основное Положение и дополнительные документы считаются раздельно. */
function headline(name: string, clauses: Clause[]): string {
  const base = clauses.filter((c) => !c.document).length;
  const docs = new Set(clauses.filter((c) => c.document).map((c) => c.document!.id)).size;
  const main = `${name || "Положение не загружено"} · пунктов: ${base}`;
  return docs ? `${main} · и ещё ${docs} ${plural(docs, ["документ", "документа", "документов"])} базы знаний` : main;
}

export function RegulationView({ focusClause, editable }: { focusClause: string | null; editable: boolean }) {
  const { data, error, loading, reload } = useRegulation();
  const toast = useToast();
  const [query, setQuery] = useState("");
  const deferred = useDeferredValue(query);
  const [spot, setSpot] = useState<string | null>(null);
  const [activeSec, setActiveSec] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);

  const shown = useMemo(() => filterClauses(data?.clauses ?? [], deferred), [data, deferred]);
  const sections = useMemo(() => groupSections(shown), [shown]);
  // номера пунктов основного Положения: только на них ссылки в тексте работают
  const known = useMemo(() => new Set((data?.clauses ?? []).filter((c) => !c.document).map((c) => c.number)), [data]);
  const [progress, setProgress] = useState(0);
  const [showTop, setShowTop] = useState(false);

  // прогресс чтения и кнопка «наверх»
  useEffect(() => {
    const onScroll = () => {
      const max = document.documentElement.scrollHeight - window.innerHeight;
      setProgress(max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0);
      setShowTop(window.scrollY > 640);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [sections]);

  const copy = (text: string, done: string) => {
    void navigator.clipboard?.writeText(text).then(
      () => toast.show(done, "ok"),
      () => toast.show("Не удалось скопировать: браузер не разрешил доступ к буферу обмена", "error"),
    );
  };
  const clauseUrl = (key: string) => `${location.origin}${location.pathname}#/rules/${encodeURIComponent(key)}`;

  // оглавление подсвечивает раздел, который сейчас на экране
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const els = Array.from(document.querySelectorAll<HTMLElement>("[data-sec]"));
    if (!els.length) return;
    const io = new IntersectionObserver(
      (entries) => {
        const vis = entries.filter((e) => e.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
        if (vis) setActiveSec((vis.target as HTMLElement).dataset.sec ?? null);
      },
      { rootMargin: "-90px 0px -65% 0px", threshold: 0 },
    );
    els.forEach((el) => io.observe(el));
    return () => io.disconnect();
  }, [sections]);

  useEffect(() => {
    if (!focusClause || !data) return;
    setQuery("");
    setSpot(focusClause);
    const t = setTimeout(() => document.getElementById(`cl-${focusClause}`)?.scrollIntoView({ block: "center", behavior: "smooth" }), 80);
    const off = setTimeout(() => setSpot(null), 2800);
    return () => (clearTimeout(t), clearTimeout(off));
  }, [focusClause, data]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setUploading(true);
    try {
      const r = await api.uploadRegulation(file);
      toast.show(`Положение загружено: пунктов — ${r.clauses}`, "ok");
      await loadRegulation(true);
      reload();
    } catch (e) {
      toast.show((e as Error).message, "error");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  let n = 0;
  return (
    <div className="reg">
      <header className="view-head">
        <div>
          <p className="eyebrow">База знаний</p>
          <h1>Положение</h1>
          <p className="muted">{data ? headline(data.name, data.clauses) : "Загружаю…"}</p>
        </div>
        {editable && (
          <div className="row">
            <input ref={fileRef} hidden type="file" accept=".txt,.md,.docx,.pdf" onChange={(e) => upload(e.target.files?.[0])} />
            <button type="button" className="btn" disabled={uploading} onClick={() => fileRef.current?.click()}>
              <Icon name="inbox" /> {uploading ? "Загружаю…" : "Заменить Положение"}
            </button>
          </div>
        )}
      </header>

      <div className="reg-grid">
        <aside className="toc" aria-label="Оглавление">
          <label className="search">
            <Icon name="search" size={16} />
            <input type="search" value={query} placeholder="Поиск по пунктам" aria-label="Поиск по Положению" onChange={(e) => setQuery(e.target.value)} />
          </label>
          {query && <p className="faint tabnum found">найдено: {shown.length}</p>}
          {!query && sections.length > 1 && (
            <p className="reading faint tabnum" aria-live="off">
              <span>
                Раздел {Math.max(1, sections.findIndex((s) => s.id === activeSec) + 1)} из {sections.length}
              </span>
              <i aria-hidden="true">
                <b style={{ width: `${Math.round(progress * 100)}%` }} />
              </i>
            </p>
          )}
          <ol role="list">
            {sections.map((s) => (
              <li key={s.id}>
                <a href={`#${s.id}`} className={activeSec === s.id ? "on" : ""} onClick={(e) => (e.preventDefault(), document.getElementById(s.id)?.scrollIntoView({ behavior: "smooth", block: "start" }))}>
                  <span>{s.title}</span>
                  <small className="tabnum">{s.clauses.length}</small>
                </a>
              </li>
            ))}
          </ol>
        </aside>

        <div className="clauses">
          {error && (
            <p className="alert" role="alert">
              {error}{" "}
              <button type="button" className="link" onClick={reload}>
                Повторить
              </button>
            </p>
          )}
          {loading && !data && (
            <div className="reg-skeleton" aria-busy="true" aria-label="Загружаю Положение">
              {Array.from({ length: 5 }, (_, i) => (
                <div key={i} className="clause skeleton" style={{ ["--i" as string]: i }}>
                  <span className="no" />
                  <span className="lines">
                    <i />
                    <i />
                  </span>
                </div>
              ))}
            </div>
          )}
          {sections.map((s) => (
            <section key={s.id} id={s.id} data-sec={s.id} className="sec">
              <h2 className="section-title">{s.title}</h2>
              {s.clauses.map((c) => {
                const sub = (c.document ? c.section : c.section.slice(1)).join(" › ");
                return (
                  <Fragment key={c.number}>
                    <article id={`cl-${c.number}`} className={`clause ${spot === c.number ? "spot" : spot ? "dim" : ""}`} style={{ ["--i" as string]: Math.min(n++, 14) }}>
                      <div className="no serif tabnum">{clauseNo(c)}</div>
                      <div>
                        {sub && <p className="faint sub">{sub}</p>}
                        <p>
                          {deferred.trim().length >= 2
                            ? highlightText(c.text, deferred).map((part, i) => (typeof part === "string" ? <Fragment key={i}>{part}</Fragment> : <mark key={i}>{part.hit}</mark>))
                            : linkRefs(c.text, known).map((part, i) =>
                                typeof part === "string" ? (
                                  <Fragment key={i}>{part}</Fragment>
                                ) : (
                                  <a key={i} className="ref" href={`#/rules/${encodeURIComponent(part.ref)}`} title={`Перейти к пункту ${part.ref}`}>
                                    {part.text}
                                  </a>
                                ),
                              )}
                        </p>
                      </div>
                      <div className="clause-tools" role="group" aria-label={`Действия с пунктом ${clauseNo(c)}`}>
                        <button type="button" onClick={() => copy(clauseUrl(c.number), `Ссылка на п. ${clauseNo(c)} скопирована`)} title="Скопировать ссылку на пункт" aria-label={`Скопировать ссылку на пункт ${clauseNo(c)}`}>
                          <Icon name="link" size={14} />
                        </button>
                        <button
                          type="button"
                          onClick={() => copy(`п. ${clauseNo(c)}${c.document ? ` документа «${c.document.name}»` : " Положения"}: ${c.text}`, `Пункт ${clauseNo(c)} скопирован`)}
                          title="Скопировать текст пункта"
                          aria-label={`Скопировать текст пункта ${clauseNo(c)}`}
                        >
                          <Icon name="copy" size={14} />
                        </button>
                      </div>
                    </article>
                  </Fragment>
                );
              })}
            </section>
          ))}
          {data && !shown.length && <p className="muted">Ничего не найдено.</p>}
        </div>
      </div>
      <button type="button" className="to-top" data-show={showTop} aria-label="Наверх" title="Наверх" tabIndex={showTop ? 0 : -1} onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}>
        <Icon name="arrowUp" size={18} />
      </button>
    </div>
  );
}
