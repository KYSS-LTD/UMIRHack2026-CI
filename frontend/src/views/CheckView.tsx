import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError } from "../api";
import { ClauseDrawer } from "../components/ClauseDrawer";
import { ChatBar } from "../components/desk/ChatBar";
import { Composer, type ComposerHandle } from "../components/desk/Composer";
import type { DeskDoc, Highlight } from "../components/desk/highlight";
import { PaperViewer } from "../components/desk/PaperViewer";
import { Thread } from "../components/desk/Thread";
import { Tray } from "../components/desk/Tray";
import { useAssistant } from "../hooks/useAssistant";
import { cardOwners, type RunInput, type Turn, useDesk } from "../hooks/useDesk";
import { useFetch } from "../hooks/useFetch";
import { useHistory } from "../hooks/useHistory";
import { useObjectUrl } from "../hooks/useObjectUrl";
import { usePagePreview } from "../hooks/usePagePreview";
import { resetActivity, setActivity } from "../lib/activity";
import { useBus } from "../lib/bus";
import { readDraft, saveDraft } from "../lib/draft";
import { isQuizState, mergeCorrection, OPEN_STATUS, optionText, statusOfferText } from "../lib/dialog";
import { fileFromClipboard, isPdf, validateFile } from "../lib/files";
import { fieldBoxes } from "../lib/format";
import { STAGES, stageIndex } from "../lib/stages";
import { aboutRegulation, suggestionsFor } from "../lib/suggestions";
import { AUTO_TYPE, type Box, type DialogState, type DocTypeSummary, type FieldResult, GENERIC_TYPE, type Health, type PartResult, type SampleItem, type Source, type SubmitResult } from "../types";
import { ConfirmDialog } from "../ui/Dialog";
import { Icon } from "../ui/Icon";
import { Portal } from "../ui/Portal";
import { useToast } from "../ui/Toasts";

interface Props {
  types: DocTypeSummary[];
  /** примеры со стола: загружает оболочка один раз (их же показывает палитра команд) */
  samples: SampleItem[];
  health: Health | null;
  onOpenClause: (ref: string) => void;
}

const isNarrow = () => typeof matchMedia === "function" && matchMedia("(max-width: 1099px)").matches;

export function CheckView({ types, samples, health, onOpenClause }: Props) {
  const toast = useToast();
  const desk = useDesk();
  const assistant = useAssistant();
  const history = useHistory();
  const rules = useFetch(api.ruleSuggestions);
  const composer = useRef<ComposerHandle>(null);
  const stageRef = useRef<HTMLElement>(null);

  const [file, setFile] = useState<File | null>(null);
  // недописанный вопрос переживает случайную перезагрузку и переход между разделами (только в этой вкладке, на диск не пишется)
  const [question, setQuestion] = useState(readDraft);
  useEffect(() => saveDraft(question), [question]);
  const [docType, setDocType] = useState(AUTO_TYPE);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [highlight, setHighlight] = useState<Highlight | null>(null);
  const [hover, setHover] = useState<Highlight | null>(null);
  const [activeField, setActiveField] = useState<string | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [drawer, setDrawer] = useState<{ ref: string; quote: string } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [forgetting, setForgetting] = useState(false);
  const [sweeping, setSweeping] = useState(false);
  const [away, setAway] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const { turns, busy } = desk;
  const maxMb = health?.max_upload_mb ?? 25;
  const lastTurn = turns[turns.length - 1];
  const activeTurn = turns.find((t) => t.id === activeId) ?? lastTurn ?? null;
  // реплика анкеты документа не показывает: стол остаётся на последней проверке
  const lastCheck = useMemo(() => [...turns].reverse().find((t) => !t.chat) ?? null, [turns]);
  const deskTurn = activeTurn && !activeTurn.chat ? activeTurn : lastCheck;
  // документ сессии на сервере — последний проверенный файл; править можно его карточку (она у первого ответа по этому файлу)
  const sessionDocTurn = assistant.ready ? ([...turns].reverse().find((t) => !t.chat && t.phase === "done" && !!t.file && !!t.result?.doc_type.id) ?? null) : null;
  const editableId = sessionDocTurn ? (cardOwners(turns).get(sessionDocTurn.id) ?? sessionDocTurn.id) : null;
  const quiz = isQuizState(assistant.dialog?.state);

  // ход проверки виден в верхней панели и в заголовке вкладки
  const wasBusy = useRef(false);
  const running = turns.find((t) => t.phase === "running" && !t.chat); // ответ анкеты — не проверка: панель и вкладка его не показывают
  const runFraction = running?.progress?.fraction ?? 0;
  const runLabel = running?.progress ? (STAGES[Math.min(stageIndex(running.progress.stage), STAGES.length - 1)]?.title ?? "Проверяю") : "";
  useEffect(() => {
    if (running) {
      wasBusy.current = true;
      setActivity({ busy: true, fraction: runFraction, label: runLabel });
    } else if (wasBusy.current) {
      wasBusy.current = false;
      setActivity({ busy: false, fraction: 1, label: "", finishedAt: Date.now() });
    }
  }, [!!running, runFraction, runLabel]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => resetActivity(), []);

  // новая реплика становится активной
  useEffect(() => {
    if (lastTurn) {
      setActiveId(lastTurn.id);
      setHover(null);
    }
  }, [lastTurn?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  // пришёл ответ: сразу показываем на снимке то, на чём он основан
  const lastDone = lastTurn?.phase === "done" ? lastTurn.result : null;
  useEffect(() => {
    if (!lastDone || !lastTurn) return;
    const all = [...lastDone.answer.sources, ...lastDone.answer.related];
    for (const s of all) {
      const boxes = s.boxes.length ? s.boxes : lastDone.fields.filter((f) => f.id === s.ref).flatMap(fieldBoxes);
      if (boxes.length) {
        setHighlight({ id: `${s.kind}:${s.ref}`, label: s.label, boxes });
        setActiveField(s.kind === "field" ? s.ref : null);
        return;
      }
    }
    setHighlight(null);
    setActiveField(null);
  }, [lastDone]); // eslint-disable-line react-hooks/exhaustive-deps

  // сессия истекла между действиями: сервер уже удалил её данные — заводим новую
  const sessionGone = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 410) void assistant.load();
    },
    [assistant],
  );

  /** Реплика в анкету; fallback — что проверить, если сообщение оказалось не про анкету. */
  const talk = useCallback(
    (display: string, text: string, fallback?: RunInput) =>
      desk.talk(
        display,
        text,
        async (signal) => {
          const res = await api.assistant.message(text, null, signal);
          assistant.setDialog(res.dialog);
          if (res.session === "expired") toast.show("Прежняя сессия истекла, её данные удалены. Начнём заново.", "info");
          return { reply: res.reply, download: res.download, passthrough: res.reply.kind === "passthrough" };
        },
        fallback ?? { file: null, question: text, docType: AUTO_TYPE },
      ),
    [desk, assistant, toast],
  );

  /** Быстрый ответ или фраза-подсказка: подпись кнопки остаётся в ленте, на сервер уходит команда анкеты. */
  const statusTicket = useRef<number | null>(null);
  const say = useCallback(
    (label: string) => {
      if (label === OPEN_STATUS) {
        location.hash = `#/status/${statusTicket.current ?? ""}`; // страница статуса — на сервер этот ответ не уходит
        return;
      }
      void talk(label, optionText(label));
    },
    [talk],
  );

  /** Обращение отправлено: секретарь сам называет номер и код и предлагает открыть страницу статуса. */
  const afterSubmit = useCallback(
    (res: SubmitResult) => {
      statusTicket.current = res.ticket_id ?? null;
      desk.note({ text: statusOfferText(res), state: "completed", kind: "info", question: null, options: [OPEN_STATUS], progress: null });
    },
    [desk],
  );

  const generate = useCallback(
    (templateId: string | null) =>
      void desk.talk("Сформировать документ", "сформировать", async (signal) => {
        try {
          const res = await api.assistant.generate(templateId, signal);
          assistant.setDialog(res.dialog);
          const reply = res.reply ?? { text: "Этот документ уже сформирован — его можно скачать.", state: res.dialog.state, kind: "done", question: null, options: [], progress: null };
          return { reply, download: res.download };
        } catch (e) {
          sessionGone(e);
          throw e;
        }
      }),
    [desk, assistant, sessionGone],
  );

  /**
   * Отправка из поля ввода. Новый файл — сразу проверка. Иначе сообщение сначала разбирает анкета
   * («Я хочу перевестись», ответы на её вопросы); всё остальное — обычная проверка с этапами и снимком.
   * direct — уточняющий вопрос к готовому ответу: всегда проверка.
   */
  const submit = useCallback(
    async (o?: { file?: File | null; question?: string; docType?: string; direct?: boolean }) => {
      const f = o && "file" in o ? (o.file ?? null) : file;
      const q = o?.question ?? question;
      const t = o?.docType ?? docType;
      setHighlight(null);
      setActiveField(null);
      setQuestion("");
      const input: RunInput = { file: f, fileName: f?.name, question: q, docType: t };
      const fileIsNew = !!f && !turns.some((x) => x.file === f);
      const viaDialog = !o?.direct && !!q.trim() && !fileIsNew && assistant.ready;
      const done = viaDialog ? await talk(q, q, input) : await desk.run(input);
      if (done?.result) {
        history.add({ id: done.id, at: done.at, question: q.trim(), fileName: f?.name ?? "", docType: t, typeTitle: done.result.doc_type.title ?? "", verdict: done.result.answer.verdict, answer: done.result.answer.text.slice(0, 160) });
      }
    },
    [file, question, docType, desk, history, turns, assistant.ready, talk],
  );

  const correct = useCallback(
    async (turn: Turn, unit: string, value: string): Promise<boolean> => {
      try {
        const res = await api.assistant.correctField(unit, value, turn.question);
        if (turn.result) desk.revise(turn.id, mergeCorrection(turn.result, res));
        toast.show("Сохранено — ответ пересчитан по исправленной карточке", "ok");
        return true;
      } catch (e) {
        toast.show((e as Error).message, "error");
        sessionGone(e);
        return false;
      }
    },
    [desk, toast, sessionGone],
  );

  const loadSample = useCallback(
    async (s: SampleItem) => {
      try {
        const res = await fetch(api.sampleUrl(s.file));
        if (!res.ok) throw new Error("Не удалось загрузить пример");
        const blob = await res.blob();
        const f = new File([blob], s.file, { type: blob.type || "image/png" });
        const t = s.doc_type === GENERIC_TYPE ? GENERIC_TYPE : AUTO_TYPE;
        setFile(f);
        setDocType(t);
        await submit({ file: f, question: s.question, docType: t });
      } catch (e) {
        toast.show((e as Error).message, "error");
      }
    },
    [submit, toast],
  );

  const reset = useCallback(() => {
    desk.clear();
    setFile(null);
    setQuestion("");
    setDocType(AUTO_TYPE);
    setHighlight(null);
    setHover(null);
    setActiveId(null);
    setActiveField(null);
  }, [desk]);

  /**
   * Одно действие для «Очистить чат» и «Удалить мои данные»: сервер забывает сессию (анкету, документ, готовые файлы), стол пустеет,
   * чат начинается с нуля — с приветствием секретаря. Если сервер данные не удалил, ничего не сбрасываем: на экране не должно быть
   * «пустого» чата при живых данных на сервере.
   */
  const wipe = useCallback(
    async (sweep: boolean) => {
      if (sweeping) return;
      setForgetting(false);
      setSweeping(true);
      desk.cancel();
      const wiped = api.assistant.deleteData().then(
        (res) => ({ res, error: null as Error | null }),
        (error: Error) => ({ res: null, error }),
      );
      if (sweep && turns.length) await new Promise((r) => setTimeout(r, 420)); // реплики «сметаются» с ленты
      const { res, error } = await wiped;
      setSweeping(false);
      if (error) {
        toast.show(`Не удалось очистить чат: ${error.message}`, "error");
        return;
      }
      reset();
      setAway(false);
      toast.show(res?.message ?? "Чат очищен, данные сессии удалены.", "ok");
      await assistant.load(); // новая пустая сессия
      composer.current?.focusQuestion();
    },
    [sweeping, desk, turns.length, reset, toast, assistant],
  );

  const offerToContinue = useCallback(
    (state: DialogState) =>
      desk.note({ text: "Анкета не завершена — можно продолжить с того же места, начать заново или прекратить.", state, kind: "info", question: null, options: ["продолжить", "заново", "прекратить"], progress: null }),
    [desk],
  );

  // анкета, начатая до перезагрузки страницы, продолжается с того же места; готовый документ можно скачать снова
  const restored = useRef(false);
  useEffect(() => {
    const info = assistant.info;
    if (!info || restored.current) return;
    restored.current = true;
    const { state } = info.dialog;
    if (isQuizState(state)) {
      offerToContinue(state);
    } else if (state === "completed" && info.downloads.length) {
      desk.note({ text: "Документ из этой сессии ещё можно скачать.", state, kind: "done", question: null, options: [], progress: null }, info.downloads[info.downloads.length - 1]);
    }
  }, [assistant.info, desk, offerToContinue]);

  const clearChat = useCallback(() => void wipe(true), [wipe]);
  const forget = useCallback(() => void wipe(false), [wipe]);

  useBus<SampleItem>("sample", (s) => void loadSample(s));
  useBus<string>("say", (text) => say(text));
  useBus("forget", () => setForgetting(true));
  useBus("new-desk", clearChat);
  useBus("pick-file", () => composer.current?.pickFile());
  useBus("focus-question", () => composer.current?.focusQuestion());
  // вход или выход: данные теперь другие (у вошедшего — общие с ботом), состояние берём у сервера заново
  useBus<"in" | "out">("account", (kind) => {
    void (async () => {
      if (kind === "out") {
        desk.cancel();
        reset();
        setAway(false);
        restored.current = true; // новая анонимная сессия пуста: предлагать продолжить нечего
        await assistant.load();
        return;
      }
      const st = await assistant.load();
      if (st && !turns.length) restored.current = false; // стол пуст: предложим продолжить анкету, начатую в боте
    })();
  });

  // файл можно бросить на любое место стола, вставить из буфера; Esc отменяет проверку; «/» — к вопросу
  useEffect(() => {
    let depth = 0;
    const hasFiles = (e: DragEvent) => !!e.dataTransfer && Array.from(e.dataTransfer.types).includes("Files");
    const onEnter = (e: DragEvent) => hasFiles(e) && (depth++, setDragging(true));
    const onLeave = (e: DragEvent) => hasFiles(e) && (depth = Math.max(0, depth - 1)) === 0 && setDragging(false);
    const onOver = (e: DragEvent) => hasFiles(e) && e.preventDefault();
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDragging(false);
      const f = e.dataTransfer?.files[0];
      if (!f || busy) return;
      const problem = validateFile(f, maxMb);
      problem ? toast.show(problem, "error") : setFile(f);
    };
    const onPaste = (e: ClipboardEvent) => {
      const f = fileFromClipboard(e);
      if (f && !busy) (setFile(f), toast.show(`Вставлено из буфера: ${f.name}`, "info"));
    };
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const typing = tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
      if (e.key === "Escape" && busy) desk.cancel();
      if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey) (e.preventDefault(), composer.current?.focusQuestion());
    };
    window.addEventListener("dragenter", onEnter);
    window.addEventListener("dragleave", onLeave);
    window.addEventListener("dragover", onOver);
    window.addEventListener("drop", onDrop);
    window.addEventListener("paste", onPaste);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("dragenter", onEnter);
      window.removeEventListener("dragleave", onLeave);
      window.removeEventListener("dragover", onOver);
      window.removeEventListener("drop", onDrop);
      window.removeEventListener("paste", onPaste);
      window.removeEventListener("keydown", onKey);
    };
  }, [busy, maxMb, toast, desk]);

  const typeTitle = (id: string) => (id === GENERIC_TYPE ? "Другой документ" : (types.find((t) => t.id === id)?.title ?? id));

  const flyToStage = () => isNarrow() && stageRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });

  const focusSource = (turn: Turn, s: Source, boxes: Box[]) => {
    setActiveId(turn.id);
    if (!boxes.length) return toast.show("Для этого источника на снимке нет отдельной рамки", "info");
    setActiveField(s.kind === "field" ? s.ref : null);
    setHighlight({ id: `${s.kind}:${s.ref}`, label: s.label, boxes });
    flyToStage();
  };

  const selectField = (turn: Turn, f: FieldResult, part?: PartResult) => {
    setActiveId(turn.id);
    setActiveField(part ? `${f.id}.${part.id}` : f.id);
    setHighlight({ id: `field:${f.id}${part ? "." + part.id : ""}`, label: part ? `${f.title}: ${part.title}` : f.title, boxes: part ? part.boxes : fieldBoxes(f) });
    flyToStage();
  };

  const chooseType = (turn: Turn, typeId: string) => {
    setDocType(typeId);
    setHighlight(null);
    void desk
      .run({ file: turn.file, fileName: turn.fileName, question: turn.question, docType: typeId }, `Пересчитано как «${typeTitle(typeId)}»`)
      .then((done) => done?.result && history.add({ id: done.id, at: done.at, question: turn.question, fileName: turn.fileName, docType: typeId, typeTitle: done.result.doc_type.title ?? "", verdict: done.result.answer.verdict, answer: done.result.answer.text.slice(0, 160) }));
  };

  // что показывать на столе: идущая или выбранная проверка, иначе файл из поля ввода, иначе пустой лоток
  const stageTurn = turns.find((t) => t.phase === "running" && !t.chat) ?? deskTurn;
  const composerFileIsNew = !!file && (!stageTurn || stageTurn.file !== file);
  const subjectFile: Blob | null = composerFileIsNew ? file : (stageTurn?.file ?? null);
  const subjectName = composerFileIsNew ? (file?.name ?? "") : (stageTurn?.fileName ?? "");
  const subjectResult = composerFileIsNew ? null : (stageTurn?.phase === "done" ? stageTurn.result : null);
  const subjectUrl = useObjectUrl(subjectFile, subjectName);
  const pdfPreview = usePagePreview(subjectFile, subjectName);
  // пока идёт повторная проверка того же файла, на столе остаётся страница из прошлого результата, а не заглушка (рамки при этом не показываются)
  const lastPages = useMemo(() => (subjectFile ? ([...turns].reverse().find((t) => t.file === subjectFile && t.phase === "done" && t.result?.images?.length)?.result?.images ?? null) : null), [turns, subjectFile]);
  const hasSubject = !!subjectFile || !!subjectResult;
  const scanning = !composerFileIsNew && stageTurn?.phase === "running";
  const progress = stageTurn?.progress?.fraction ?? 0;
  const stageLabel = stageTurn?.progress ? (STAGES[Math.min(stageIndex(stageTurn.progress.stage), STAGES.length - 1)]?.title ?? "") : "";

  const allBoxes = useMemo(() => (subjectResult ? subjectResult.fields.flatMap(fieldBoxes) : []), [subjectResult]);

  // все документы, которые клали на стол в этой сессии, плюс файл из поля ввода
  const docs = useMemo<DeskDoc[]>(() => {
    const list: DeskDoc[] = [];
    const seen = new Set<Blob>();
    for (const t of turns) {
      if (t.file && !seen.has(t.file)) {
        seen.add(t.file);
        list.push({ key: t.id, file: t.file, name: t.fileName });
      }
    }
    if (file && !seen.has(file)) list.push({ key: "new", file, name: file.name });
    return list;
  }, [turns, file]);
  const activeDoc = docs.findIndex((d) => d.file === subjectFile);

  const selectDocByFile = (f: Blob) => {
    const i = docs.findIndex((d) => d.file === f);
    if (i >= 0) selectDoc(i);
  };

  const selectDoc = (i: number) => {
    const d = docs[i];
    if (!d) return;
    setFile(d.file as File);
    const last = [...turns].reverse().find((t) => t.file === d.file);
    if (last) setActiveId(last.id);
    setHighlight(null);
    setHover(null);
    setActiveField(null);
  };
  const suggestions = quiz ? [] : aboutRegulation(suggestionsFor(types, docType, deskTurn?.result?.doc_type.id, rules.data?.rules ?? [], assistant.ready), !!file);
  const ruleTexts = useMemo(() => new Set((rules.data?.rules ?? []).map((r) => r.text)), [rules.data]);

  return (
    <div className="desk">
      <section className="desk-chat" aria-label="Диалог">
        {turns.length > 0 && <ChatBar count={turns.filter((t) => !t.botOnly).length} busy={busy} quiz={quiz} onClear={clearChat} onStop={() => say("Прекратить анкету")} />}
        <div
          className="chat-scroll"
          ref={scrollRef}
          onScroll={(e) => {
            const el = e.currentTarget;
            setAway(el.scrollHeight - el.scrollTop - el.clientHeight > 260);
          }}
        >
          <Thread
            turns={turns}
            activeId={activeTurn?.id ?? null}
            types={types}
            busy={busy}
            activeField={activeField}
            history={history.entries}
            onActivate={(id) => {
              setActiveId(id);
              setHighlight(null);
              setActiveField(null);
            }}
            onCancel={desk.cancel}
            onRetry={(t) => (t.chat ? void talk(t.question, t.said ?? t.question) : void desk.run({ file: t.file, fileName: t.fileName, question: t.question, docType: t.docType }))}
            onFocusSource={focusSource}
            onClause={(s) => setDrawer({ ref: s.ref, quote: s.quote })}
            onFieldHover={setHover}
            onFieldSelect={selectField}
            onChooseType={chooseType}
            onPickHistory={(e) => {
              setQuestion(e.question);
              setDocType(e.docType);
              composer.current?.focusQuestion();
            }}
            onClearHistory={history.clear}
            rules={(rules.data?.rules ?? []).map((r) => r.text)}
            // подсказка о правилах — вопрос к Положению, без документа; остальное — по тому же документу
            onAsk={(t, q) => void submit({ file: ruleTexts.has(q) ? null : (t.file as File | null), question: q, docType: t.docType, direct: true })}
            onEdit={(t) => {
              setQuestion(t.question);
              setDocType(t.docType);
              if (t.file) selectDocByFile(t.file);
              composer.current?.focusQuestion();
            }}
            dialog={assistant.dialog}
            templates={assistant.templates}
            delivery={assistant.delivery}
            onSay={say}
            onGenerate={generate}
            onSent={afterSubmit}
            editableId={busy ? null : editableId}
            onCorrect={correct}
            sweeping={sweeping}
            onTrySample={
              samples.length
                ? () => {
                    const demo = samples.find((s) => /нет|не хватает/i.test(s.title)) ?? samples[0]!; // лучше всего показывает ценность: «чего не хватает»
                    void loadSample(demo);
                  }
                : undefined
            }
          />
        </div>
        {away && turns.length > 0 && (
          <button type="button" className="to-latest" aria-label="К последнему сообщению" title="К последнему сообщению" onClick={() => scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" })}>
            <Icon name="chevronDown" size={18} />
          </button>
        )}
        <Composer
          ref={composer}
          file={file}
          question={question}
          docType={docType}
          types={types}
          suggestions={suggestions}
          maxMb={maxMb}
          busy={busy}
          onFile={setFile}
          onQuestion={setQuestion}
          onDocType={setDocType}
          onSubmit={() => void submit()}
          onCancel={desk.cancel}
          onError={(m) => toast.show(m, "error")}
          recall={lastTurn?.question}
          placeholder={quiz ? "Ваш ответ на вопрос анкеты" : undefined}
          checked={!!file && turns.some((t) => t.file === file)}
        />
      </section>

      <section className={`desk-stage ${hasSubject ? "has-subject" : "is-empty"}`} ref={stageRef} aria-label="Стол с документом">
        {hasSubject ? (
          <PaperViewer
            src={subjectUrl ?? pdfPreview?.data_url ?? null}
            fileSize={subjectFile?.size}
            text={subjectResult?.ocr.text?.trim() || undefined}
            onFindText={
              stageTurn?.file
                ? (phrase) => void submit({ file: stageTurn.file as File | null, question: `Где на документе «${phrase.replace(/[«»“”"]/g, " ").replace(/\s+/g, " ").trim()}»?`, docType: stageTurn.docType, direct: true })
                : undefined
            }
            srcSize={!subjectUrl && pdfPreview ? { w: pdfPreview.width, h: pdfPreview.height } : null}
            pages={subjectResult?.images?.length ? subjectResult.images : scanning ? lastPages : null}
            fileName={subjectName}
            pdf={!!subjectFile && isPdf({ name: subjectName, type: subjectFile.type })}
            scanning={!!scanning}
            progress={progress}
            stageLabel={stageLabel}
            highlight={highlight}
            hover={hover}
            allBoxes={allBoxes}
            showAll={showAll}
            revealKey={`${stageTurn?.id}:${stageTurn?.phase}`}
            onToggleAll={() => setShowAll((v) => !v)}
            onClear={() => (setHighlight(null), setActiveField(null))}
            docs={docs}
            activeDoc={activeDoc}
            busy={busy}
            onSelectDoc={selectDoc}
            onAnother={() => composer.current?.pickFile()}
            cta={composerFileIsNew && !busy ? { label: question.trim() ? "Проверить с моим вопросом" : "Проверить документ целиком", onClick: () => void submit() } : null}
          />
        ) : (
          <Tray maxMb={maxMb} dragging={dragging} busy={busy} onFile={setFile} onError={(m) => toast.show(m, "error")} />
        )}
      </section>

      <Portal>
        <ConfirmDialog
          open={forgetting}
          title="Удалить мои данные?"
          message="Чат начнётся с нуля. Будут удалены анкета, карточка проверенного документа и готовые файлы этой сессии. Обращения, уже переданные в деканат, остаются у деканата. Отменить удаление нельзя."
          confirmLabel="Удалить"
          danger
          onConfirm={forget}
          onCancel={() => setForgetting(false)}
        />
        <ClauseDrawer refId={drawer?.ref ?? null} quote={drawer?.quote ?? ""} onClose={() => setDrawer(null)} onOpenRegulation={(ref) => (setDrawer(null), onOpenClause(ref))} />
        <div className="drop-veil" data-on={dragging} aria-hidden="true">
          <div>
            <strong>Отпустите — положу на стол</strong>
            <span>JPG, PNG или PDF</span>
          </div>
        </div>
      </Portal>
    </div>
  );
}
