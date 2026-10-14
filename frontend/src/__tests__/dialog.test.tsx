import { act, fireEvent, render, renderHook, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AnswerTurn } from "../components/desk/AnswerTurn";
import { DialogTurn } from "../components/desk/DialogTurn";
import { cropAround } from "../components/desk/highlight";
import { SnippetCrop } from "../components/desk/SnippetCrop";
import { FieldsCard } from "../components/desk/FieldsCard";
import { Thread } from "../components/desk/Thread";
import { NavBar } from "../components/NavBar";
import { cardOwners, trimTurns, type Turn, useDesk } from "../hooks/useDesk";
import { isQuizState, markCorrected, mergeCorrection, optionText, parseReview, splitQuestion } from "../lib/dialog";
import { followUps } from "../lib/followups";
import { aboutRegulation, suggestionsFor } from "../lib/suggestions";
import type { AnalysisResult, DialogInfo, DialogReply, FieldResult } from "../types";

const noop = () => {};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const field = (over: Partial<FieldResult>): FieldResult => ({
  id: "student_name",
  title: "ФИО студента",
  export_title: "ФИО студента",
  kind: "person",
  status: "found",
  found: true,
  value: "Иванов Иван",
  normalized: null,
  confidence: 0.9,
  method: "label",
  evidence: "",
  boxes: [{ page: 0, x0: 1, y0: 2, x1: 3, y1: 4 }],
  parts: [],
  ...over,
});

const result = (over: Partial<AnalysisResult> = {}): AnalysisResult => ({
  doc_type: { id: "study_certificate", title: "Справка об обучении", how: "auto", confidence: 0.9, uncertain: false, ranking: [], candidates: [], reason: "" },
  ocr: { engine: "tesseract", pages: [], text: "" },
  fields: [
    field({}),
    field({
      id: "number_date",
      title: "Номер и дата",
      kind: "composite",
      status: "partial",
      value: "№ 15",
      parts: [
        { id: "number", title: "Номер", found: true, value: "15", normalized: "15", confidence: 0.9, boxes: [{ page: 0, x0: 5, y0: 5, x1: 6, y1: 6 }] },
        { id: "date", title: "Дата", found: false, value: "", normalized: null, confidence: 0, boxes: [] },
      ],
    }),
  ],
  answer: { verdict: "missing", text: "Не хватает даты.", intent: "check", sources: [], missing: [], related: [], confidence: 0.8, notes: [], debug: {} },
  warnings: [],
  timings: {},
  images: [],
  export: {},
  ...over,
});

const reply = (over: Partial<DialogReply> = {}): DialogReply => ({ text: "Привет", state: "idle", kind: "info", question: null, options: [], progress: null, ...over });
const dialog = (state: DialogInfo["state"]): DialogInfo => ({ state, scenario: "transfer", current: null, editing: false, case_id: 1, answered: [], version: 1 });
const chatTurn = (r: DialogReply, over: Partial<Turn> = {}): Turn => ({
  id: "c1",
  at: Date.now(),
  question: "Я хочу перевестись",
  docType: "auto",
  fileName: "",
  file: null,
  phase: "done",
  progress: null,
  result: null,
  error: null,
  chat: true,
  reply: r,
  ...over,
});

describe("dialog helpers", () => {
  it("maps quick-reply labels to questionnaire commands", () => {
    expect(optionText("Да, начать")).toBe("да");
    expect(optionText("Сформировать документ")).toBe("сформировать");
    expect(optionText("Прекратить анкету")).toBe("отмена");
    expect(optionText("прекратить")).toBe("прекратить"); // слово сервер понимает и так
    expect(optionText("очная")).toBe("очная");
  });

  it("knows when the questionnaire is in progress", () => {
    expect(["idle", "screening", "review", "document_generation", "completed"].map((s) => isQuizState(s as DialogInfo["state"]))).toEqual([false, true, true, true, false]);
    expect(isQuizState(null)).toBe(false);
  });

  it("splits the intro, the progress label and the question", () => {
    expect(splitQuestion("Начинаем анкету.\n\nВопрос 2 из 6. На каком курсе вы учитесь?")).toEqual({ intro: "Начинаем анкету.", label: "Вопрос 2 из 6", body: "На каком курсе вы учитесь?" });
    expect(splitQuestion("Вопрос 1 из 6. Как вас зовут?")).toEqual({ intro: "", label: "Вопрос 1 из 6", body: "Как вас зовут?" });
    expect(splitQuestion("Просто текст")).toEqual({ intro: "", label: "", body: "Просто текст" });
  });

  it("parses the review sheet of the questionnaire", () => {
    const text = "Продолжаем с того же места.\n\nПроверьте собранные данные:\n1. ФИО: Иванов Иван Иванович\n2. Текущий курс: 2\n3. Паспортные данные: — (пропущено)\nДата заявления: 13.10.2026\nОбратите внимание:\n— Задолженности: есть\nВсё верно?";
    const sheet = parseReview(text)!;
    expect(sheet.items).toEqual([
      { title: "ФИО", value: "Иванов Иван Иванович", skipped: false },
      { title: "Текущий курс", value: "2", skipped: false },
      { title: "Паспортные данные", value: "— (пропущено)", skipped: true },
    ]);
    expect(sheet.date).toBe("13.10.2026");
    expect(sheet.notes).toEqual(["Задолженности: есть"]);
    expect(sheet.tail).toEqual(["Всё верно?"]);
    expect(sheet.head).toEqual(["Продолжаем с того же места."]);
    expect(parseReview("Без пунктов")).toBeNull();
  });

  it("merges a field correction: new values and answer, old boxes, corrected marks", () => {
    const before = result();
    const fresh = result().fields.map((f) => ({ ...f, boxes: [], parts: f.parts.map((p) => ({ ...p, boxes: [] })) }));
    fresh[1] = { ...fresh[1]!, status: "found", value: "№ 15 от 01.09.2026", parts: [fresh[1]!.parts[0]!, { ...fresh[1]!.parts[1]!, found: true, value: "01.09.2026" }] };
    const answer = { ...before.answer, verdict: "ok" as const, text: "Всё на месте." };
    const after = mergeCorrection(before, { document: { filename: "a.png", doc_type: { id: "study_certificate", title: null, how: "auto" }, fields: [], warnings: [], corrected: ["number_date.date"] }, fields: fresh, answer });
    const nd = after.fields[1]!;
    expect(nd.status).toBe("found");
    expect(nd.corrected).toBe(true);
    expect(nd.parts[1]).toMatchObject({ found: true, value: "01.09.2026", corrected: true });
    expect(nd.parts[0]!.boxes).toEqual(before.fields[1]!.parts[0]!.boxes);
    expect(after.fields[0]!.boxes).toEqual(before.fields[0]!.boxes);
    expect(after.fields[0]!.corrected).toBe(false);
    expect(after.answer.text).toBe("Всё на месте.");
  });

  it("marks fields corrected earlier when the same file is checked again", () => {
    const r = markCorrected(result({ corrected: ["student_name", "number_date.date"] }));
    expect(r.fields[0]!.corrected).toBe(true);
    expect(r.fields[1]!.corrected).toBe(true);
    expect(r.fields[1]!.parts.map((p) => !!p.corrected)).toEqual([false, true]);
    const same = result();
    expect(markCorrected(same)).toBe(same);
  });

  it("offers the questionnaire among suggestions only when a session exists", () => {
    expect(suggestionsFor([], "auto", null, []).some((s) => s.kind === "form")).toBe(false);
    expect(suggestionsFor([], "auto", null, [], true).at(-1)).toEqual({ kind: "form", text: "Я хочу перевестись" });
  });
});

describe("trimTurns", () => {
  it("keeps short questionnaire turns longer than heavy document checks", () => {
    const checks = Array.from({ length: 12 }, (_, i) => ({ id: `k${i}` }) as Turn);
    const chats = Array.from({ length: 5 }, (_, i) => ({ id: `c${i}`, chat: true }) as Turn);
    const out = trimTurns([...checks, ...chats]);
    expect(out.filter((t) => !t.chat).map((t) => t.id)).toEqual(checks.slice(2).map((t) => t.id));
    expect(out.filter((t) => t.chat)).toHaveLength(5);
  });
});

describe("DialogTurn", () => {
  const base = { busy: false, dialog: dialog("screening"), templates: [], delivery: null, onReply: noop, onGenerate: noop };

  it("shows the question with its progress and quick replies on the latest turn", async () => {
    const onReply = vi.fn();
    const r = reply({ kind: "question", state: "screening", text: "Начинаем.\n\nВопрос 3 из 6. Какая у вас форма обучения?", options: ["очная", "заочная"], progress: [3, 6] });
    const { container } = render(<DialogTurn {...base} turn={chatTurn(r)} latest onReply={onReply} />);
    expect(screen.getByText("Вопрос 3 из 6")).toBeInTheDocument();
    expect(screen.getByText("Начинаем.")).toBeInTheDocument();
    expect(container.querySelectorAll(".quiz-ticks i.done")).toHaveLength(2);
    expect(container.querySelector(".quiz-ticks i.now")).not.toBeNull();
    await userEvent.click(within(screen.getByRole("group", { name: "Быстрые ответы" })).getByRole("button", { name: "заочная" }));
    expect(onReply).toHaveBeenCalledWith("заочная");
  });

  it("hides quick replies on earlier turns", () => {
    render(<DialogTurn {...base} turn={chatTurn(reply({ options: ["Да, начать", "Нет"] }))} latest={false} />);
    expect(screen.queryByRole("group", { name: "Быстрые ответы" })).not.toBeInTheDocument();
  });

  it("renders the review as a sheet", () => {
    const r = reply({ kind: "review", state: "review", text: "Проверьте собранные данные:\n1. ФИО: Иванов Иван\n2. Текущий курс: 2\nВсё верно?", options: ["Верно", "Исправить"] });
    const { container } = render(<DialogTurn {...base} dialog={dialog("review")} turn={chatTurn(r)} latest />);
    expect(screen.getByText("Лист сверки")).toBeInTheDocument();
    expect(container.querySelectorAll(".review-sheet li")).toHaveLength(2);
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
  });

  it("generates the document from the chosen template instead of a plain quick reply", async () => {
    const onGenerate = vi.fn();
    const r = reply({ kind: "generation", state: "document_generation", text: "Данные подтверждены.", options: ["Сформировать документ"] });
    const templates = [
      { id: "perevod", name: "Заявление о переводе", placeholders: [] },
      { id: "zachislenie", name: "Заявление о зачислении", placeholders: [] },
    ];
    render(<DialogTurn {...base} dialog={dialog("document_generation")} templates={templates} turn={chatTurn(r)} latest onGenerate={onGenerate} />);
    expect(screen.queryByRole("group", { name: "Быстрые ответы" })).not.toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "zachislenie" } });
    await userEvent.click(screen.getByRole("button", { name: /Сформировать документ/ }));
    expect(onGenerate).toHaveBeenCalledWith("zachislenie");
  });

  it("explains that there are no templates yet", () => {
    render(<DialogTurn {...base} dialog={dialog("document_generation")} turn={chatTurn(reply({ kind: "generation", options: ["Сформировать документ"] }))} latest />);
    expect(screen.getByText(/Шаблонов документов пока нет/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Сформировать документ/ })).not.toBeInTheDocument();
  });

  it("offers the ready document and, after the questionnaire, a voluntary submission", () => {
    const download = { id: "d1", url: "/api/assistant/download/d1", filename: "Заявление о переводе.docx" };
    render(<DialogTurn {...base} dialog={dialog("completed")} delivery={{ channel: "queue", available: true, reason: "" }} turn={chatTurn(reply({ kind: "done", text: "Документ готов." }), { download })} latest />);
    expect(screen.getByRole("link", { name: /Скачать/ })).toHaveAttribute("href", "/api/assistant/download/d1");
    expect(screen.getByRole("button", { name: /Передать в деканат/ })).toBeInTheDocument();
  });
});

describe("FieldsCard corrections", () => {
  it("edits a field in place and sends the unit and the new value", async () => {
    const onCorrect = vi.fn().mockResolvedValue(true);
    render(<FieldsCard fields={result().fields} activeId={null} onHover={noop} onSelect={noop} onCorrect={onCorrect} />);
    await userEvent.click(screen.getByRole("button", { name: "Исправить: ФИО студента" }));
    const input = screen.getByRole("textbox", { name: "Новое значение: ФИО студента" });
    expect(input).toHaveValue("Иванов Иван");
    await userEvent.clear(input);
    await userEvent.type(input, "Петров Пётр{Enter}");
    expect(onCorrect).toHaveBeenCalledWith("student_name", "Петров Пётр");
    await waitFor(() => expect(screen.queryByRole("textbox")).not.toBeInTheDocument());
  });

  it("edits parts of a composite field separately and keeps the editor when the server refuses", async () => {
    const onCorrect = vi.fn().mockResolvedValue(false);
    render(<FieldsCard fields={result().fields} activeId={null} onHover={noop} onSelect={noop} onCorrect={onCorrect} />);
    expect(screen.queryByRole("button", { name: "Исправить: Номер и дата" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Исправить: Номер и дата: Дата" }));
    await userEvent.type(screen.getByRole("textbox"), "32.13.2026{Enter}");
    expect(onCorrect).toHaveBeenCalledWith("number_date.date", "32.13.2026");
    expect(screen.getByRole("textbox")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("is read-only without a session document and shows corrected values", () => {
    render(<FieldsCard fields={[field({ corrected: true })]} activeId={null} onHover={noop} onSelect={noop} />);
    expect(screen.queryByRole("button", { name: /Исправить/ })).not.toBeInTheDocument();
    expect(screen.getByText("исправлено")).toBeInTheDocument();
  });
});

describe("Thread empty state", () => {
  const props = {
    turns: [],
    activeId: null,
    types: [],
    busy: false,
    activeField: null,
    history: [],
    onActivate: noop,
    onCancel: noop,
    onRetry: noop,
    onFocusSource: noop,
    onClause: noop,
    onFieldHover: noop,
    onFieldSelect: noop,
    onChooseType: noop,
    onPickHistory: noop,
    onClearHistory: noop,
    rules: [],
    onAsk: noop,
    onEdit: noop,
    dialog: dialog("idle"),
    templates: [],
    delivery: null,
    onSay: noop,
    onGenerate: noop,
    editableId: null,
    onCorrect: async () => true,
  };

  it("lists what the secretary does as a numbered ledger, not as cards", () => {
    render(<Thread {...props} />);
    const ledger = screen.getByRole("list", { name: "Что умеет секретарь" });
    expect(within(ledger).getAllByRole("listitem")).toHaveLength(4);
    expect(ledger).toHaveTextContent("Собираю заявление");
    expect(document.querySelector(".caps")).toBeNull();
  });

  it("starts the questionnaire or a question from the table of contents", async () => {
    const onSay = vi.fn();
    render(<Thread {...props} onSay={onSay} />);
    await userEvent.click(screen.getByRole("button", { name: /Я хочу перевестись/ }));
    expect(onSay).toHaveBeenCalledWith("Я хочу перевестись");
  });

  it("does not clutter the thread with storage notes", () => {
    render(<Thread {...props} />);
    expect(screen.queryByText(/хранятся на сервере/)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Удалить мои данные" })).not.toBeInTheDocument();
  });
});

describe("useDesk questionnaire turns", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows a questionnaire reply in the same thread", async () => {
    const { result: hook } = renderHook(() => useDesk());
    let promise: Promise<unknown> = Promise.resolve();
    act(() => {
      promise = hook.current.talk("Я хочу перевестись", "Я хочу перевестись", async () => ({ reply: reply({ kind: "offer", options: ["Да, начать", "Нет"] }), download: null }));
    });
    expect(hook.current.turns[0]).toMatchObject({ chat: true, phase: "running" });
    await act(async () => {
      await promise;
    });
    expect(hook.current.turns[0]).toMatchObject({ chat: true, phase: "done", question: "Я хочу перевестись" });
    expect(hook.current.turns[0]!.reply?.options).toEqual(["Да, начать", "Нет"]);
  });

  it("turns a message that is not about the questionnaire into a regular check", async () => {
    const res = { answer: { verdict: "ok", text: "10 дней" }, fields: [], images: [], warnings: [] };
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => json(String(url) === "/api/jobs" ? { id: "j1" } : { id: "j1", status: "done", stage: "done", label: "Готово", fraction: 1, elapsed: 1, result: res }));
    const { result: hook } = renderHook(() => useDesk());
    await act(async () => {
      await hook.current.talk("Сколько дней?", "Сколько дней?", async () => ({ reply: reply({ kind: "passthrough" }), download: null, passthrough: true }), { question: "Сколько дней?", docType: "auto" });
    });
    await waitFor(() => expect(hook.current.turns[0]!.phase).toBe("done"), { timeout: 4000 });
    expect(hook.current.turns).toHaveLength(1);
    expect(hook.current.turns[0]).toMatchObject({ chat: false, question: "Сколько дней?" });
    expect(hook.current.turns[0]!.result).toMatchObject({ answer: { text: "10 дней" } });
  });

  it("adds a secretary-only note and bumps the revision on a correction", () => {
    const { result: hook } = renderHook(() => useDesk());
    act(() => hook.current.note(reply({ text: "Анкета не завершена", options: ["продолжить", "заново"] })));
    expect(hook.current.turns[0]).toMatchObject({ botOnly: true, chat: true, phase: "done" });
    const id = hook.current.turns[0]!.id;
    act(() => hook.current.revise(id, result()));
    expect(hook.current.turns[0]!.revision).toBe(1);
  });
});

describe("NavBar without a separate assistant page", () => {
  it("has no «Ассистент» link: the questionnaire lives on the desk", () => {
    render(<NavBar route="check" onRoute={noop} theme="system" onThemeSet={noop} onPalette={noop} onHelp={noop} />);
    expect(screen.queryByRole("link", { name: /Ассистент/, hidden: true })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Вход для сотрудников/, hidden: true })).toHaveAttribute("href", "/admin");
  });
});

describe("document card is shown once per file", () => {
  const file = new File(["x"], "s.png", { type: "image/png" });
  const check = (id: string, over: Partial<Turn> = {}): Turn => ({ ...chatTurn(reply()), id, chat: false, reply: undefined, file, fileName: "s.png", result: result(), ...over });

  it("gives follow-up questions about the same file only the answer", () => {
    const other = new File(["y"], "b.png", { type: "image/png" });
    const turns = [
      check("a"),
      chatTurn(reply(), { id: "q" }),
      check("b"),
      check("c", { result: result({ doc_type: { ...result().doc_type, id: "academic_leave" } }) }), // тип пересчитан — новая карточка
      check("d", { file: other }),
      check("e"),
      check("rules", { file: null, fileName: "" }),
    ];
    const owners = cardOwners(turns);
    expect(Object.fromEntries(owners)).toEqual({ a: "a", b: "a", c: "c", d: "d", e: "a" });
    expect(owners.has("rules")).toBe(false);
  });

  const props = {
    turn: check("b"),
    active: true,
    types: [],
    busy: false,
    activeField: null,
    rules: [],
    onActivate: noop,
    onFocusSource: noop,
    onClause: noop,
    onFieldHover: noop,
    onFieldSelect: noop,
    onChooseType: noop,
    onAsk: noop,
  };

  it("renders the answer without repeating the card and links back to it", async () => {
    const onShowCard = vi.fn();
    render(<AnswerTurn {...props} docCard={false} onShowCard={onShowCard} />);
    expect(screen.getAllByText("Не хватает даты.").length).toBeGreaterThan(0);
    expect(screen.queryByText("Что найдено в документе")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(onShowCard).toHaveBeenCalledTimes(1);
  });

  it("keeps the card on the first answer about the file", () => {
    render(<AnswerTurn {...props} />);
    expect(screen.getByText("Что найдено в документе")).toBeInTheDocument();
  });
});

describe("answers by the document, regulation only when named", () => {
  const rules = [
    { kind: "rules" as const, text: "Сколько дней рассматривается заявление?" },
    { kind: "rules" as const, text: "Что сказано в Положении о справке?" },
    { kind: "check" as const, text: "Чего не хватает в документе?" },
  ];

  it("names the regulation in rule suggestions while a document is on the desk", () => {
    const out = aboutRegulation(rules, true);
    expect(out[0]!.text).toBe("По Положению: сколько дней рассматривается заявление?");
    expect(out[1]!.text).toBe("Что сказано в Положении о справке?"); // уже называет Положение
    expect(out[2]!.text).toBe("Чего не хватает в документе?"); // вопрос о документе не меняется
  });

  it("leaves suggestions alone when there is no document", () => {
    expect(aboutRegulation(rules, false)).toBe(rules);
  });

  it("offers the same question «по Положению» first when the document has no answer", () => {
    const r = result({ answer: { ...result().answer, verdict: "no_answer", intent: "document", text: "В загруженном документе нет ответа", debug: { regulation_question: "Сколько дней рассматривается заявление — по Положению?" } } });
    const list = followUps(r, "Сколько дней рассматривается заявление?", []);
    expect(list[0]).toBe("Сколько дней рассматривается заявление — по Положению?");
    expect(followUps(result(), "Что-то", [])[0]).not.toContain("по Положению");
  });
});

describe("snippet crop keeps the proportions of the photo", () => {
  it("sizes the box by the crop ratio so the background is never stretched", () => {
    const boxes = [{ page: 0, x0: 400, y0: 900, x1: 700, y1: 1100 }];
    const { container } = render(<SnippetCrop src="blob:x" width={1000} height={1400} boxes={boxes} label="печать" />);
    const el = container.querySelector(".snippet-img") as HTMLElement;
    const g = cropAround(boxes, 1000, 1400)!;
    expect(Number(el.style.getPropertyValue("--r"))).toBeCloseTo(g.w / g.h, 5);
    // фон масштабируется по осям так, что снимок сохраняет свои пропорции (1000 × 1400)
    const [bw, bh] = el.style.backgroundSize.split(" ").map((v) => parseFloat(v));
    expect((bw! * (g.w / 100)) / (bh! * (g.h / 100))).toBeCloseTo(1000 / 1400, 4);
    expect(el.style.maxHeight).toBe("");
  });
});
