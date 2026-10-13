import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, runAnalysis } from "../api";
import { markCorrected } from "../lib/dialog";
import type { AnalysisResult, DialogReply, DownloadInfo, JobSnapshot } from "../types";

export interface RunInput {
  file?: Blob | null;
  fileName?: string;
  question: string;
  docType: string;
}

export type TurnPhase = "running" | "done" | "error" | "cancelled";

/** Один обмен в диалоге на столе: «вопрос → разбор → ответ» или реплика анкеты. */
export interface Turn {
  id: string;
  at: number;
  question: string;
  docType: string;
  fileName: string;
  file: Blob | null;
  /** пояснение для повторного запуска: «Пересчитано как …» */
  note?: string;
  phase: TurnPhase;
  progress: JobSnapshot | null;
  result: AnalysisResult | null;
  error: string | null;
  /** реплика анкеты, а не проверка документа */
  chat?: boolean;
  /** что ушло на сервер (подпись быстрого ответа и команда анкеты различаются) */
  said?: string;
  reply?: DialogReply | null;
  download?: DownloadInfo | null;
  /** только реплика секретаря, без вопроса студента (продолжение анкеты после перезагрузки) */
  botOnly?: boolean;
  /** растёт при правке полей: заключение пересчитано и появляется заново */
  revision?: number;
}

/** Ответ анкеты; passthrough — сообщение не про анкету, его разбирает обычная проверка. */
export interface ChatOutcome {
  reply: DialogReply;
  download: DownloadInfo | null;
  passthrough?: boolean;
}

const MAX_TURNS = 40;
const MAX_CHECKS = 10; // проверки держат снимки и превью страниц — их храним меньше, чем короткие реплики анкеты
let seq = 0;
const uid = () => `t${Date.now().toString(36)}${(seq++).toString(36)}`;

export function trimTurns(list: Turn[]): Turn[] {
  let out = list.slice(-MAX_TURNS);
  const checks = out.filter((t) => !t.chat);
  if (checks.length > MAX_CHECKS) {
    const drop = new Set(checks.slice(0, checks.length - MAX_CHECKS).map((t) => t.id));
    out = out.filter((t) => !drop.has(t.id));
  }
  return out;
}

/**
 * Карточка документа показывается один раз — у первой проверки файла (с тем же типом документа).
 * Уточняющие вопросы по тому же файлу дают только ответ. Возвращает: id проверки → id проверки с карточкой.
 * Проверки без файла (вопросы по Положению) в словарь не попадают.
 */
export function cardOwners(turns: Turn[]): Map<string, string> {
  const first = new Map<Blob, Map<string, string>>();
  const out = new Map<string, string>();
  for (const t of turns) {
    if (t.chat || t.phase !== "done" || !t.result || !t.file) continue;
    const type = t.result.doc_type.id ?? `:${t.result.doc_type.how}`;
    let byType = first.get(t.file);
    if (!byType) first.set(t.file, (byType = new Map()));
    if (!byType.has(type)) byType.set(type, t.id);
    out.set(t.id, byType.get(type)!);
  }
  return out;
}

const queued = (): JobSnapshot => ({ id: "", status: "queued", stage: "queued", label: "Отправляю на сервер", fraction: 0.02, elapsed: 0 });
const failure = (e: unknown, fallback: string) => (e instanceof ApiError ? e.message : fallback);

export function useDesk() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const ctrl = useRef<AbortController | null>(null);
  const jobId = useRef<string | null>(null);
  const activeId = useRef<string | null>(null);

  const patch = useCallback((id: string, change: Partial<Turn>) => setTurns((list) => list.map((t) => (t.id === id ? { ...t, ...change } : t))), []);

  useEffect(() => () => ctrl.current?.abort(), []);

  const cancel = useCallback(() => {
    ctrl.current?.abort();
    if (jobId.current) void api.cancelJob(jobId.current);
    const id = activeId.current;
    if (id) patch(id, { phase: "cancelled", progress: null });
    activeId.current = null;
  }, [patch]);

  const begin = useCallback(() => {
    const mine = new AbortController();
    ctrl.current = mine;
    jobId.current = null;
    return mine;
  }, []);

  /** Проверка документа (или вопрос по Положению). reuse — реплика анкеты, которая оказалась обычным вопросом: она становится проверкой. */
  const run = useCallback(
    async (input: RunInput, note?: string, reuse?: Turn): Promise<Turn | null> => {
      if (activeId.current && activeId.current !== reuse?.id) cancel();
      const base = {
        question: input.question.trim(),
        docType: input.docType,
        fileName: input.fileName ?? "",
        file: input.file ?? null,
        note,
        phase: "running" as const,
        progress: queued(),
        result: null,
        error: null,
        chat: false,
        reply: undefined,
      };
      const turn: Turn = reuse ? { ...reuse, ...base, question: reuse.question || base.question } : { id: uid(), at: Date.now(), ...base };
      const id = turn.id;
      if (reuse) patch(id, turn);
      else setTurns((list) => trimTurns([...list, turn]));
      const mine = reuse && ctrl.current && !ctrl.current.signal.aborted ? ctrl.current : begin();
      activeId.current = id;
      try {
        const result = markCorrected(
          await runAnalysis(
            input,
            { onProgress: (progress) => !mine.signal.aborted && patch(id, { progress }), onJobId: (jid) => (jobId.current = jid) },
            mine.signal,
          ),
        );
        if (mine.signal.aborted) return null;
        activeId.current = null;
        const done: Turn = { ...turn, phase: "done", progress: null, result };
        patch(id, { phase: "done", progress: null, result });
        return done;
      } catch (e) {
        if (mine.signal.aborted || (e as Error)?.name === "AbortError") return null;
        activeId.current = null;
        patch(id, { phase: "error", progress: null, error: failure(e, "Не удалось выполнить проверку") });
        return null;
      }
    },
    [begin, cancel, patch],
  );

  /**
   * Реплика в анкету. Пока ждём ответ — «секретарь печатает». Если сообщение не про анкету и есть fallback,
   * та же реплика становится обычной проверкой (с этапами и снимком).
   */
  const talk = useCallback(
    async (display: string, said: string, call: (signal: AbortSignal) => Promise<ChatOutcome>, fallback?: RunInput): Promise<Turn | null> => {
      if (activeId.current) cancel();
      const turn: Turn = {
        id: uid(),
        at: Date.now(),
        question: display.trim(),
        docType: fallback?.docType ?? "auto",
        fileName: "",
        file: null,
        phase: "running",
        progress: null,
        result: null,
        error: null,
        chat: true,
        said,
        reply: null,
      };
      setTurns((list) => trimTurns([...list, turn]));
      const mine = begin();
      activeId.current = turn.id;
      try {
        const out = await call(mine.signal);
        if (mine.signal.aborted) return null;
        if (out.passthrough && fallback) return run(fallback, undefined, turn);
        activeId.current = null;
        const done: Turn = { ...turn, phase: "done", reply: out.reply, download: out.download };
        patch(turn.id, { phase: "done", reply: out.reply, download: out.download });
        return done;
      } catch (e) {
        if (mine.signal.aborted || (e as Error)?.name === "AbortError") return null;
        activeId.current = null;
        patch(turn.id, { phase: "error", error: failure(e, "Секретарь не смог ответить") });
        return null;
      }
    },
    [begin, cancel, patch, run],
  );

  /** Реплика секретаря без вопроса (например, «анкета не завершена — продолжить?» после перезагрузки страницы). */
  const note = useCallback((reply: DialogReply, download: DownloadInfo | null = null) => {
    const turn: Turn = { id: uid(), at: Date.now(), question: "", docType: "auto", fileName: "", file: null, phase: "done", progress: null, result: null, error: null, chat: true, botOnly: true, reply, download };
    setTurns((list) => trimTurns([...list, turn]));
  }, []);

  /** Правка карточки полей: новый результат и следующая «ревизия» заключения. */
  const revise = useCallback(
    (id: string, result: AnalysisResult) => setTurns((list) => list.map((t) => (t.id === id ? { ...t, result, revision: (t.revision ?? 0) + 1 } : t))),
    [],
  );

  const clear = useCallback(() => {
    cancel();
    setTurns([]);
  }, [cancel]);

  const busy = turns.some((t) => t.phase === "running");
  return { turns, run, talk, note, revise, cancel, clear, busy };
}
