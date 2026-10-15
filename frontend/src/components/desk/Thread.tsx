import { useEffect, useMemo, useRef } from "react";
import { cardOwners, type Turn } from "../../hooks/useDesk";
import { emit } from "../../lib/bus";
import { relativeTime, VERDICT_META } from "../../lib/format";
import type { HistoryEntry } from "../../lib/history";
import type { Box, Delivery, DialogInfo, DocTypeSummary, FieldResult, PartResult, Source, SubmitResult, TemplateInfo } from "../../types";
import { Icon } from "../../ui/Icon";
import { AnswerTurn } from "./AnswerTurn";
import { Avatar } from "./Avatar";
import { DialogTurn, TypingTurn } from "./DialogTurn";
import type { Highlight } from "./highlight";
import { ProcessTurn } from "./ProcessTurn";
import { UserTurn } from "./UserTurn";

interface Props {
  turns: Turn[];
  activeId: string | null;
  types: DocTypeSummary[];
  busy: boolean;
  activeField: string | null;
  history: HistoryEntry[];
  onActivate: (id: string) => void;
  onCancel: () => void;
  onRetry: (turn: Turn) => void;
  onFocusSource: (turn: Turn, s: Source, boxes: Box[]) => void;
  onClause: (s: Source) => void;
  onFieldHover: (h: Highlight | null) => void;
  onFieldSelect: (turn: Turn, f: FieldResult, p?: PartResult) => void;
  onChooseType: (turn: Turn, typeId: string) => void;
  onPickHistory: (e: HistoryEntry) => void;
  onClearHistory: () => void;
  rules: string[];
  onAsk: (turn: Turn, question: string) => void;
  onEdit: (turn: Turn) => void;
  /** анкета и документ сессии */
  dialog: DialogInfo | null;
  templates: TemplateInfo[];
  delivery: Delivery | null;
  onSay: (text: string, display?: string) => void;
  onGenerate: (templateId: string | null) => void;
  onSent?: (result: SubmitResult) => void;
  /** проверка, чью карточку можно исправить (документ текущей сессии) */
  editableId: string | null;
  onCorrect: (turn: Turn, unit: string, value: string) => Promise<boolean>;
  /** идёт очистка чата: реплики уходят с ленты, затем стол пустеет */
  sweeping?: boolean;
  /** «Попробовать на примере»: проверка готового документа со стола (кнопки нет, если примеров нет) */
  onTrySample?: () => void;
  /** «Войти через Telegram»: есть, только пока вход возможен (бот включён, человек не вошёл и не в Mini App) */
  onLogin?: () => void;
}

/** Что умеет секретарь — как опись в деле: римская нумерация на полях, тонкие линейки, без плашек. */
const LEDGER: { title: string; text: string }[] = [
  { title: "Читаю снимок", text: "фото, скан или PDF; наклон, тень и блики не помеха." },
  { title: "Заполняю карточку", text: "ФИО, вуз, период, номер, печать; чего нет — пишу «не найдено», неверное можно поправить." },
  { title: "Отвечаю по Положению", text: "со ссылкой на пункт и подсветкой места на снимке." },
  { title: "Собираю заявление", text: "короткая анкета о переводе или зачислении, готовый DOCX и, если захотите, отправка в деканат." },
];
const ROMAN = ["I", "II", "III", "IV", "V"];

/** С чего начать — как оглавление: вопрос, отточие, что получится. Вопросы по Положению приходят с сервера (они берутся из загруженного Положения). */
const FORMS: [string, string][] = [
  ["Я хочу перевестись", "анкета и заявление"],
  ["Как к вам поступить?", "анкета на зачисление"],
];
const DEFAULT_ASK = "Сколько дней рассматривается заявление?";

/** Первая строка «Можно начать так»: выделена, потому что на неё нажимает тот, кто не знает, с чего начать. */
const FEATURED: [string, string] = ["Что ты умеешь?", "всё, что я могу, и с чего начать"];

export function Thread(p: Props) {
  const end = useRef<HTMLDivElement>(null);
  const last = p.turns[p.turns.length - 1];
  const lastId = last?.id;
  const lastPhase = last?.phase;

  // новая реплика или готовый ответ — плавно прокручиваем диалог вниз
  useEffect(() => {
    if (!lastId) return;
    const t = setTimeout(() => end.current?.scrollIntoView({ behavior: "smooth", block: "end" }), 80);
    return () => clearTimeout(t);
  }, [lastId, lastPhase]);

  const asks = (p.rules.length ? p.rules : [DEFAULT_ASK]).slice(0, 3);
  const starters: [string, string][] = [...FORMS, ...asks.map((q): [string, string] => [q, "ответ по Положению"])];

  const typeTitle = (id: string) => p.types.find((t) => t.id === id)?.title ?? id;
  const owners = useMemo(() => cardOwners(p.turns), [p.turns]);

  // «Показать карточку»: раскрыть первый ответ по этому файлу и прокрутить к нему
  const showCard = (id: string) => {
    p.onActivate(id);
    setTimeout(() => document.getElementById(`turn-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }), 120);
  };

  if (!p.turns.length) {
    return (
      <div className="thread-empty">
        <h1>
          Покажите документ — получите <em>ответ со ссылкой на пункт</em>
        </h1>
        <ol className="ledger" aria-label="Что умеет секретарь">
          {LEDGER.map((c, i) => (
            <li key={c.title} style={{ ["--i" as string]: i }}>
              <span className="ledger-no" aria-hidden="true">
                {ROMAN[i]}
              </span>
              <p>
                <strong>{c.title}</strong> — {c.text}
              </p>
            </li>
          ))}
        </ol>
        <section className="starts" aria-labelledby="starts-title">
          <h2 id="starts-title" className="eyebrow">
            Можно начать так
          </h2>
          <ul role="list">
            <li className="featured" style={{ ["--i" as string]: 0 }}>
              <button type="button" className="start-line" disabled={p.busy} onClick={() => p.onSay(FEATURED[0])}>
                <span className="start-q">
                  <Icon name="question" size={18} className="start-q-icon" />
                  <span className="start-q-text">{FEATURED[0]}</span>
                </span>
                <span className="start-hint">{FEATURED[1]}</span>
                <Icon name="arrowRight" size={14} className="start-arrow" />
              </button>
            </li>
            {starters.map(([q, hint], i) => (
              <li key={q} style={{ ["--i" as string]: i + 1 }}>
                <button type="button" className="start-line" disabled={p.busy} onClick={() => p.onSay(q)}>
                  <span className="start-q">«{q}»</span>
                  <span className="start-hint">{hint}</span>
                  <Icon name="arrowRight" size={14} className="start-arrow" />
                </button>
              </li>
            ))}
          </ul>
          <div className="start-actions" role="group" aria-label="Или сразу">
            <button type="button" className="start-act" disabled={p.busy} onClick={() => emit("pick-file")}>
              <Icon name="attach" size={15} /> Проверить документ
            </button>
            {p.onTrySample && (
              <button type="button" className="start-act" disabled={p.busy} onClick={p.onTrySample}>
                <Icon name="doc" size={15} /> Попробовать на примере
              </button>
            )}
            <button type="button" className="start-act" onClick={() => (location.hash = "#/status")}>
              <Icon name="inbox" size={15} /> Статус обращения
            </button>
            {p.onLogin && (
              <button type="button" className="start-act" onClick={p.onLogin}>
                <Icon name="telegram" size={15} /> Войти через Telegram
              </button>
            )}
          </div>
        </section>
        {p.history.length > 0 && (
          <section className="hist" aria-labelledby="hist-title">
            <div className="hist-head">
              <h2 id="hist-title">
                <Icon name="history" size={16} /> Недавние вопросы
              </h2>
              <button type="button" className="link" onClick={p.onClearHistory}>
                Очистить
              </button>
            </div>
            <ul role="list">
              {p.history.slice(0, 4).map((e, i) => (
                <li key={e.id} style={{ ["--i" as string]: i }}>
                  <button type="button" className="hist-card" onClick={() => p.onPickHistory(e)}>
                    <span className={`dot tone-${VERDICT_META[e.verdict].tone}`} aria-label={VERDICT_META[e.verdict].title} />
                    <span className="hist-main">
                      <strong>{e.question || (e.fileName ? `Файл «${e.fileName}»` : "Без вопроса")}</strong>
                      <small>{e.answer}</small>
                    </span>
                    <small className="faint nowrap">{relativeTime(e.at)}</small>
                  </button>
                </li>
              ))}
            </ul>
            <p className="hint">История хранится только в этом браузере, снимки в неё не попадают.</p>
          </section>
        )}
      </div>
    );
  }

  return (
    <div className="thread" role="log" aria-label="Диалог с секретарём" data-sweeping={p.sweeping || undefined}>
      {p.turns.map((t, i) => (
        <div className="exchange" key={t.id} id={`turn-${t.id}`} style={{ ["--k" as string]: Math.min(p.turns.length - 1 - i, 8) }}>
          {!t.botOnly && <UserTurn turn={t} typeTitle={typeTitle} onEdit={t.chat ? undefined : p.onEdit} />}
          {t.phase === "running" && (t.chat ? <TypingTurn /> : <ProcessTurn turn={t} onCancel={p.onCancel} />)}
          {t.phase === "done" && t.chat && t.reply && (
            <DialogTurn
              turn={t}
              latest={t.id === lastId}
              busy={p.busy}
              dialog={p.dialog}
              templates={p.templates}
              delivery={p.delivery}
              onReply={(label) => p.onSay(label)}
              onGenerate={p.onGenerate}
              onSent={p.onSent}
            />
          )}
          {t.phase === "done" && !t.chat && t.result && (
            <AnswerTurn
              turn={t}
              active={t.id === p.activeId}
              types={p.types}
              busy={p.busy}
              activeField={t.id === p.activeId ? p.activeField : null}
              onActivate={() => p.onActivate(t.id)}
              onFocusSource={(s, boxes) => p.onFocusSource(t, s, boxes)}
              onClause={p.onClause}
              onFieldHover={p.onFieldHover}
              onFieldSelect={(f, part) => p.onFieldSelect(t, f, part)}
              onChooseType={(id) => p.onChooseType(t, id)}
              rules={p.rules}
              onAsk={(q) => p.onAsk(t, q)}
              editable={t.id === p.editableId && !p.busy}
              onCorrect={(unit, value) => p.onCorrect(t, unit, value)}
              docCard={(owners.get(t.id) ?? t.id) === t.id}
              onShowCard={() => showCard(owners.get(t.id) ?? t.id)}
            />
          )}
          {t.phase === "error" && (
            <article className="turn turn-bot error-turn" role="alert">
              <Avatar />
              <div className="bubble bot-bubble err">
                <p className="eyebrow">Не получилось</p>
                <h3>{t.chat ? "Секретарь не смог ответить" : "Проверка не завершилась"}</h3>
                <p>{t.error}</p>
                <button type="button" className="btn small" onClick={() => p.onRetry(t)}>
                  <Icon name="refresh" size={14} /> Попробовать ещё раз
                </button>
              </div>
            </article>
          )}
          {t.phase === "cancelled" && (
            <article className="turn turn-bot cancelled">
              <Avatar />
              <div className="bubble bot-bubble muted-bubble">
                <p>{t.chat ? "Сообщение не отправлено." : "Проверка отменена."}</p>
                <button type="button" className="link" onClick={() => p.onRetry(t)}>
                  {t.chat ? "Отправить снова" : "Запустить снова"}
                </button>
              </div>
            </article>
          )}
        </div>
      ))}
      <div ref={end} className="thread-end" />
    </div>
  );
}
