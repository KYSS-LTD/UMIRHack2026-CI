import { useEffect, useState } from "react";
import type { Turn } from "../../hooks/useDesk";
import { parseReview, type ReviewSheet, splitQuestion } from "../../lib/dialog";
import type { Delivery, DialogInfo, DownloadInfo, SubmitResult, TemplateInfo } from "../../types";
import { Icon } from "../../ui/Icon";
import { Reveal } from "../../ui/Reveal";
import { Avatar } from "./Avatar";
import { NextSteps } from "./NextSteps";
import { SubmitPanel } from "./SubmitPanel";

interface Props {
  turn: Turn;
  /** последняя реплика ленты: только у неё быстрые ответы и кнопка «Сформировать документ» */
  latest: boolean;
  busy: boolean;
  dialog: DialogInfo | null;
  templates: TemplateInfo[];
  delivery: Delivery | null;
  onReply: (label: string) => void;
  onGenerate: (templateId: string | null) => void;
  /** обращение отправлено: секретарь пишет номер, код и предлагает страницу статуса */
  onSent?: (result: SubmitResult) => void;
}

const TONE: Record<string, string> = { error: "bad", clarify: "ask", done: "ok", cancelled: "quiet" };

/** «Вопрос 3 из 6»: отметки пройденных вопросов, текущий — пульсирует. */
function QuizMeter({ n, total, label }: { n: number; total: number; label: string }) {
  return (
    <div className="quiz-meter">
      <span className="eyebrow">{label || `Вопрос ${n} из ${total}`}</span>
      <span className="quiz-ticks" aria-hidden="true">
        {Array.from({ length: total }, (_, i) => (
          <i key={i} className={i < n - 1 ? "done" : i === n - 1 ? "now" : ""} style={{ ["--i" as string]: i }} />
        ))}
      </span>
    </div>
  );
}

/** Лист сверки анкеты: как ведомость — номер, графа, значение через отточие. */
function Review({ sheet }: { sheet: ReviewSheet }) {
  return (
    <div className="review-sheet">
      {sheet.head.map((line) => (
        <p key={line} className="memo-intro">
          {line}
        </p>
      ))}
      <p className="eyebrow">Лист сверки</p>
      {sheet.fixed && <p className="rv-fixed">{sheet.fixed}</p>}
      <ol>
        {sheet.items.map((it, i) => (
          <li key={it.title} className={it.skipped ? "skipped" : ""} style={{ ["--i" as string]: i }}>
            <span className="rv-no tabnum">{i + 1}</span>
            <span className="rv-title">{it.title}</span>
            <span className="rv-lead" aria-hidden="true" />
            <span className="rv-value">{it.value}</span>
          </li>
        ))}
      </ol>
      {sheet.date && <p className="rv-date">Дата заявления: {sheet.date}</p>}
      {sheet.notes.length > 0 && (
        <ul className="v-notes" role="list">
          {sheet.notes.map((n) => (
            <li key={n}>
              <Icon name="alert" size={14} />
              <span>{n}</span>
            </li>
          ))}
        </ul>
      )}
      {sheet.tail.map((line) => (
        <p key={line} className="memo-text">
          {line}
        </p>
      ))}
    </div>
  );
}

function GenerateBar({ templates, busy, onGenerate }: { templates: TemplateInfo[]; busy: boolean; onGenerate: (id: string | null) => void }) {
  const [id, setId] = useState(templates[0]?.id ?? "");
  useEffect(() => {
    if (!templates.some((t) => t.id === id)) setId(templates[0]?.id ?? "");
  }, [templates, id]);

  if (!templates.length) {
    return (
      <p className="gen-empty">
        <Icon name="alert" size={14} />
        <span>Шаблонов документов пока нет: администратор ещё не загрузил DOCX-шаблон. Ответы анкеты сохранены на время сессии.</span>
      </p>
    );
  }
  return (
    <div className="gen-bar">
      {templates.length > 1 ? (
        <label className="gen-pick">
          <span className="eyebrow">Шаблон</span>
          <select value={id} disabled={busy} onChange={(e) => setId(e.target.value)}>
            {templates.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
      ) : (
        <span className="gen-pick">
          <span className="eyebrow">Шаблон</span>
          <strong>{templates[0]!.name}</strong>
        </span>
      )}
      <button type="button" className="btn primary" disabled={busy} onClick={() => onGenerate(id || null)}>
        <Icon name="doc" size={16} /> Сформировать документ
      </button>
    </div>
  );
}

/** Готовый документ: листок с печатью въезжает в реплику, рядом — скачивание. */
export function ReadyDoc({ download }: { download: DownloadInfo }) {
  return (
    <div className="ready-doc">
      <span className="ready-sheet" aria-hidden="true">
        <i />
        <i />
        <i />
        <b />
      </span>
      <span className="ready-text">
        <strong>Документ готов</strong>
        <small title={download.filename}>{download.filename}</small>
      </span>
      <a className="btn primary small" href={download.url} download>
        <Icon name="download" size={15} /> Скачать
      </a>
    </div>
  );
}

/** Секретарь «пишет»: три чернильные точки, пока сервер разбирает ответ анкеты. */
export function TypingTurn() {
  return (
    <article className="turn turn-bot typing-turn" aria-live="polite" aria-busy="true">
      <Avatar busy />
      <div className="bubble bot-bubble">
        <span className="ink-dots" role="status" aria-label="Секретарь пишет">
          <i />
          <i />
          <i />
        </span>
      </div>
    </article>
  );
}

/** Реплика анкеты: вопрос с отметкой хода, лист сверки, быстрые ответы, формирование DOCX и (по желанию) отправка в деканат. */
export function DialogTurn(p: Props) {
  const reply = p.turn.reply!;
  const q = splitQuestion(reply.text);
  const review = reply.kind === "review" ? parseReview(reply.text) : null;
  const generating = p.latest && p.dialog?.state === "document_generation";
  const options = reply.options.filter((o) => !(generating && o === "Сформировать документ"));
  const tone = TONE[reply.kind] ?? "";

  return (
    <article className={`turn turn-bot memo-turn ${tone}`}>
      <Avatar />
      <div className="bubble bot-bubble">
        {(q.intro || reply.progress || review || q.body) && (
          <div className={`memo ${tone}`} role={reply.kind === "error" ? "alert" : undefined}>
            {q.intro && <p className="memo-intro">{q.intro}</p>}
            {reply.progress && <QuizMeter n={reply.progress[0]} total={reply.progress[1]} label={q.label} />}
            {review ? (
              <Review sheet={review} />
            ) : (
              q.body && (
                <p className="memo-text">
                  <Reveal text={q.body} />
                </p>
              )
            )}
          </div>
        )}
        {p.latest && options.length > 0 && (
          <div className="replies" role="group" aria-label="Быстрые ответы">
            {options.map((o, i) => (
              <button key={o} type="button" className="reply-chip" disabled={p.busy} style={{ ["--i" as string]: i }} onClick={() => p.onReply(o)}>
                {o}
              </button>
            ))}
          </div>
        )}
        {generating && <GenerateBar templates={p.templates} busy={p.busy} onGenerate={p.onGenerate} />}
        {p.turn.download && <ReadyDoc download={p.turn.download} />}
        {p.latest && p.turn.download && p.dialog?.state === "completed" && (
          <>
            <NextSteps />
            <SubmitPanel delivery={p.delivery} onSent={p.onSent} />
          </>
        )}
      </div>
    </article>
  );
}
