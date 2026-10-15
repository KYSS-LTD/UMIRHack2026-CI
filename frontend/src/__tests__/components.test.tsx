import { act, fireEvent, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { parseHash, staffCanEdit } from "../App";
import { ChatBar } from "../components/desk/ChatBar";
import { Composer } from "../components/desk/Composer";
import { Tray } from "../components/desk/Tray";
import { FieldsCard } from "../components/desk/FieldsCard";
import { TypeRail } from "../components/desk/TypeRail";
import { VerdictCard } from "../components/desk/VerdictCard";
import { useDesk } from "../hooks/useDesk";
import { clauseNo } from "../lib/format";
import { suggestionsFor } from "../lib/suggestions";
import type { Answer, DocTypeInfo, DocTypeSummary, FieldResult, Health, PagePreview } from "../types";
import { filterClauses, groupSections, highlightText } from "../views/RegulationView";

const answer = (over: Partial<Answer> = {}): Answer => ({
  verdict: "missing",
  text: "Не хватает даты выдачи и печати вуза. Эти поля обязательны по п. 3.4 Положения.",
  intent: "check",
  sources: [{ kind: "clause", ref: "3.4", label: "п. 3.4", quote: "Справка должна содержать дату выдачи и печать.", boxes: [] }],
  missing: [{ unit: "issue.date", title: "Дата выдачи", clause: "3.4" }],
  related: [],
  confidence: 0.9,
  notes: ["Качество снимка невысокое"],
  debug: {},
  ...over,
});

const noop = () => {};

describe("VerdictCard", () => {
  const props = { warnings: [], images: [] as PagePreview[], resolveBoxes: () => [], onFocusSource: noop, onClause: noop };

  it("shows the verdict, the answer text, missing items and notes", () => {
    render(<VerdictCard {...props} answer={answer()} />);
    expect(screen.getByRole("heading", { name: "Чего-то не хватает" })).toBeInTheDocument();
    expect(screen.getByTestId("answer-text")).toHaveTextContent("Не хватает даты выдачи");
    expect(screen.getByText("Дата выдачи")).toBeInTheDocument();
    expect(screen.getByText("Качество снимка невысокое")).toBeInTheDocument();
    expect(screen.getByRole("img", { name: /Штамп: Не хватает/ })).toBeInTheDocument();
  });

  it("always reminds that the final decision belongs to the dean's office", () => {
    const { rerender } = render(<VerdictCard {...props} answer={answer()} />);
    expect(screen.getByText(/Окончательное решение принимает сотрудник деканата/)).toBeInTheDocument();
    rerender(<VerdictCard {...props} answer={answer({ report: { disclaimer: "Предварительная оценка.", explanation: "Проще говоря: не хватает даты." } })} />);
    expect(screen.getByText("Предварительная оценка.")).toBeInTheDocument();
    expect(screen.getByText(/Проще говоря: не хватает даты\./)).toBeInTheDocument();
  });

  it("expands a clause quote and opens the clause", async () => {
    const onClause = vi.fn();
    render(<VerdictCard {...props} answer={answer()} onClause={onClause} />);
    await userEvent.click(screen.getByTitle("Показать текст пункта"));
    expect(screen.getByText("Справка должна содержать дату выдачи и печать.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Открыть в Положении/ }));
    expect(onClause).toHaveBeenCalledWith(expect.objectContaining({ ref: "3.4" }));
  });

  it("shows a snippet of the photo for a document source and focuses it on click", async () => {
    const onFocusSource = vi.fn();
    const box = { page: 0, x0: 100, y0: 200, x1: 300, y1: 240 };
    const a = answer({ verdict: "ok", missing: [], sources: [{ kind: "document", ref: "i0:1.2", label: "п. 1.2 документа", quote: "q", boxes: [box] }] });
    const images: PagePreview[] = [{ page: 0, width: 1000, height: 1400, data_url: "data:image/jpeg;base64,AAAA" }];
    render(<VerdictCard {...props} images={images} answer={a} resolveBoxes={(s) => s.boxes} onFocusSource={onFocusSource} />);
    expect(screen.getByText(/Фрагмент снимка/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "п. 1.2 документа" }));
    expect(onFocusSource).toHaveBeenCalledTimes(1);
  });
});

const types: DocTypeSummary[] = [
  {
    id: "study_certificate",
    title: "Справка об обучении",
    short_title: "Справка",
    source: "s.yaml",
    fields: [],
    examples: [],
    suggestions: [
      { kind: "check", text: "Хватит ли справки?" },
      { kind: "field", text: "Какая форма обучения?" },
    ],
  },
];

describe("Composer", () => {
  const base = {
    file: null,
    question: "",
    docType: "auto",
    types,
    suggestions: suggestionsFor(types, "study_certificate", null, []),
    maxMb: 25,
    busy: false,
    onFile: noop,
    onQuestion: noop,
    onDocType: noop,
    onSubmit: noop,
    onCancel: noop,
    onError: noop,
  };

  it("disables sending until there is a file or a question", () => {
    const { rerender } = render(<Composer {...base} />);
    expect(screen.getByRole("button", { name: "Проверить" })).toBeDisabled();
    rerender(<Composer {...base} question="Сколько дней?" />);
    expect(screen.getByRole("button", { name: "Проверить" })).toBeEnabled();
  });

  it("sends on Enter but not on Shift+Enter", () => {
    const onSubmit = vi.fn();
    render(<Composer {...base} question="Что в документе?" onSubmit={onSubmit} />);
    const box = screen.getByLabelText("Вопрос");
    fireEvent.keyDown(box, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(box, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("lets the user pick a type by hand, including 'other document'", async () => {
    const onDocType = vi.fn();
    render(<Composer {...base} onDocType={onDocType} />);
    await userEvent.click(screen.getByRole("button", { name: /Тип документа: Определить автоматически/ }));
    await userEvent.click(screen.getByRole("option", { name: /Справка об обучении/ }));
    expect(onDocType).toHaveBeenCalledWith("study_certificate");
    await userEvent.click(screen.getByRole("button", { name: /Тип документа/ }));
    await userEvent.click(screen.getByRole("option", { name: /Другой документ/ }));
    expect(onDocType).toHaveBeenCalledWith("generic");
  });

  it("says what to do at the current step", () => {
    const { rerender } = render(<Composer {...base} />);
    expect(screen.getByText("Приложите документ или задайте вопрос")).toBeInTheDocument();
    rerender(<Composer {...base} file={new File(["x"], "a.png", { type: "image/png" })} />);
    expect(screen.getByText(/Вопрос необязателен/)).toBeInTheDocument();
    const checked = new File(["x"], "a.png", { type: "image/png" });
    rerender(<Composer {...base} file={checked} checked />);
    expect(screen.getByText("Документ проверен — можно уточнить вопросом")).toBeInTheDocument(); // «нажмите Проверить» после проверки не нужно
    rerender(<Composer {...base} question="Что в документе?" />);
    expect(screen.queryByText(/Вопрос необязателен/)).not.toBeInTheDocument();
    expect(screen.getByText(/отправить/)).toBeInTheDocument(); // с текстом вопроса — привычная подсказка про Enter
  });

  it("explains the send button, also when it is not available yet", () => {
    const { rerender } = render(<Composer {...base} />);
    expect(screen.getByRole("button", { name: "Проверить" })).toHaveAttribute("title", "Сначала приложите документ или напишите вопрос");
    rerender(<Composer {...base} question="Сколько дней?" />);
    expect(screen.getByRole("button", { name: "Проверить" })).toHaveAttribute("title", "Проверить (Enter)");
  });

  it("offers to stop while busy", async () => {
    const onCancel = vi.fn();
    render(<Composer {...base} question="x" busy onCancel={onCancel} />);
    await userEvent.click(screen.getByRole("button", { name: "Остановить проверку" }));
    expect(onCancel).toHaveBeenCalled();
  });
});

describe("staffCanEdit", () => {
  const health = (over: Partial<Health>): Health => ({ version: "1", allow_edit: true, max_upload_mb: 25, ocr: {}, doc_types: [], regulation: { name: "", clauses: 0 }, llm: { backend: "none" }, ...over });

  it("shows the replace-regulation button only to a logged-in member of staff", () => {
    expect(staffCanEdit(health({ can_edit: true, admin: { authenticated: true } }))).toBe(true);
    expect(staffCanEdit(health({ can_edit: false, admin: { authenticated: false } }))).toBe(false);
    expect(staffCanEdit(health({ allow_edit: true }))).toBe(false);
    expect(staffCanEdit(null)).toBe(false);
  });

  it("stays hidden for everyone in the open (legacy) mode and when editing is switched off", () => {
    expect(staffCanEdit(health({ can_edit: true, admin: { authenticated: false, protect_edit: false } }))).toBe(false);
    expect(staffCanEdit(health({ can_edit: false, allow_edit: false, admin: { authenticated: true } }))).toBe(false);
  });
});

describe("Tray hover", () => {
  const mount = () => render(<Tray maxMb={25} dragging={false} busy={false} onFile={noop} onError={noop} />);

  it("tilts the stack of sheets towards the mouse and levels it when the mouse leaves", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 400, height: 300, right: 400, bottom: 300, x: 0, y: 0, toJSON: () => ({}) });
    const { container } = mount();
    const tray = container.querySelector(".tray")!;
    const sheets = container.querySelector<HTMLElement>(".sheets")!;
    fireEvent.pointerMove(tray, { clientX: 400, clientY: 0 }); // правый верхний угол
    expect(sheets.style.getPropertyValue("--ry")).toBe("9.0deg");
    expect(sheets.style.getPropertyValue("--rx")).toBe("7.0deg");
    fireEvent.pointerLeave(tray);
    expect(sheets.style.getPropertyValue("--ry")).toBe("0deg");
    expect(sheets.style.getPropertyValue("--rx")).toBe("0deg");
    vi.restoreAllMocks();
  });

  it("does not tilt for a finger on a touch screen", () => {
    const { container } = mount();
    const sheets = container.querySelector<HTMLElement>(".sheets")!;
    fireEvent.pointerMove(container.querySelector(".tray")!, { clientX: 10, clientY: 10, pointerType: "touch" });
    expect(sheets.style.getPropertyValue("--ry")).toBe("");
  });
});

describe("ChatBar", () => {
  const bar = (over: Partial<React.ComponentProps<typeof ChatBar>> = {}) => ({ count: 2, busy: false, quiz: false, onClear: vi.fn(), onStop: vi.fn(), ...over });

  it("has no strip with the stop button while there is no questionnaire", () => {
    render(<ChatBar {...bar()} />);
    expect(screen.queryByRole("button", { name: /Прекратить/ })).toBeNull();
    expect(screen.queryByText("Идёт анкета")).toBeNull();
    expect(screen.getByRole("button", { name: /Очистить чат/ })).toBeInTheDocument();
  });

  it("shows who is on the line and which document is on the desk", () => {
    const { rerender } = render(<ChatBar {...bar({ count: 1, docName: "sample_02.png", typeTitle: "Справка об обучении" })} />);
    expect(screen.getByText("на связи")).toBeInTheDocument();
    expect(screen.getByText("1 обмен")).toBeInTheDocument();
    expect(screen.getByTitle("sample_02.png · Справка об обучении")).toBeInTheDocument();
    rerender(<ChatBar {...bar({ busy: true, docName: "sample_02.png" })} />);
    expect(screen.queryByText("на связи")).not.toBeInTheDocument(); // пока секретарь отвечает, «на связи» не пишем
    expect(screen.getByText("секретарь отвечает…")).toBeInTheDocument();
    rerender(<ChatBar {...bar()} />);
    expect(document.querySelector(".cb-doc")).toBeNull(); // без документа плашки нет
  });

  it("shows the strip while the questionnaire is running; the header keeps its own status", () => {
    render(<ChatBar {...bar({ quiz: true })} />);
    expect(screen.getByText("Идёт анкета")).toBeInTheDocument();
    expect(screen.getByText("2 обмена")).toBeInTheDocument();
  });

  it("asks before stopping the questionnaire and stops only after the confirmation", async () => {
    const props = bar({ quiz: true });
    render(<ChatBar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Прекратить анкету" }));
    expect(props.onStop).not.toHaveBeenCalled();
    expect(screen.getByText("Ответы удалятся.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Прекратить" }));
    expect(props.onStop).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "Прекратить анкету" })).toBeInTheDocument(); // вопрос снят
  });

  it("'Нет' and Esc keep the questionnaire", async () => {
    const props = bar({ quiz: true });
    render(<ChatBar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Прекратить анкету" }));
    await userEvent.click(screen.getByRole("button", { name: "Нет" }));
    expect(screen.queryByText("Ответы удалятся.")).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Прекратить анкету" }));
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByText("Ответы удалятся.")).toBeNull();
    expect(props.onStop).not.toHaveBeenCalled();
  });

  it("the strip goes away when the questionnaire ends by itself", async () => {
    const props = bar({ quiz: true });
    const { rerender } = render(<ChatBar {...props} />);
    await userEvent.click(screen.getByRole("button", { name: "Прекратить анкету" }));
    rerender(<ChatBar {...props} quiz={false} />);
    expect(screen.queryByText("Ответы удалятся.")).toBeNull();
    expect(screen.queryByRole("button", { name: /Прекратить/ })).toBeNull();
  });

  it("cannot be stopped while the secretary is answering", () => {
    render(<ChatBar {...bar({ quiz: true, busy: true })} />);
    expect(screen.getByRole("button", { name: "Прекратить анкету" })).toBeDisabled();
  });
});

describe("TypeRail", () => {
  const info = (over: Partial<DocTypeInfo>): DocTypeInfo => ({
    id: null,
    title: null,
    how: "auto",
    confidence: 0,
    uncertain: false,
    ranking: [],
    candidates: [{ id: "study_certificate", title: "Справка об обучении", share: 0.4 }],
    reason: "",
    ...over,
  });

  it("asks to pick a type when the document is foreign and recalculates on choice", async () => {
    const onChoose = vi.fn();
    render(<TypeRail info={info({})} types={types} busy={false} hasFile onChoose={onChoose} />);
    expect(screen.getByText(/не похож ни на один/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("radio", { name: /Справка об обучении/ }));
    expect(onChoose).toHaveBeenCalledWith("study_certificate");
  });

  it("marks the current type and the generic mode", () => {
    render(<TypeRail info={info({ id: "study_certificate", title: "Справка об обучении", confidence: 0.9 })} types={types} busy={false} hasFile onChoose={noop} />);
    expect(screen.getByRole("radio", { name: /Справка об обучении/ })).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("radio", { name: /Другой документ/ })).toHaveAttribute("aria-checked", "false");
  });

  it("renders nothing without a file", () => {
    const { container } = render(<TypeRail info={info({})} types={types} busy={false} hasFile={false} onChoose={noop} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("FieldsCard", () => {
  const field = (over: Partial<FieldResult>): FieldResult => ({
    id: "f",
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
    boxes: [{ page: 0, x0: 1, y0: 2, x1: 30, y1: 12 }],
    parts: [],
    ...over,
  });

  it("lists found fields and writes «не найдено» for missing ones", () => {
    render(<FieldsCard fields={[field({}), field({ id: "g", title: "Номер", status: "missing", found: false, value: "", boxes: [] })]} activeId={null} onHover={noop} onSelect={noop} />);
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.getByText("не найдено")).toBeInTheDocument();
    expect(screen.getByText("из 2")).toBeInTheDocument();
  });

  it("tells that a field can be clicked, and about the pencil only when the card is editable", () => {
    const { rerender } = render(<FieldsCard fields={[field({})]} activeId={null} onHover={noop} onSelect={noop} />);
    expect(screen.getByText(/Нажмите на поле — покажу это место на снимке/)).toBeInTheDocument();
    expect(screen.queryByText(/Прочитано неверно/)).not.toBeInTheDocument();
    rerender(<FieldsCard fields={[field({})]} activeId={null} onHover={noop} onSelect={noop} onCorrect={async () => true} />);
    expect(screen.getByText(/Прочитано неверно\? Нажмите на карандаш/)).toBeInTheDocument();
  });

  it("highlights on hover and selects on click", async () => {
    const onHover = vi.fn();
    const onSelect = vi.fn();
    render(<FieldsCard fields={[field({})]} activeId={null} onHover={onHover} onSelect={onSelect} />);
    const row = screen.getByRole("button", { name: /ФИО студента: показать на снимке/ });
    await userEvent.hover(row);
    expect(onHover).toHaveBeenCalledWith(expect.objectContaining({ label: "ФИО студента" }));
    await userEvent.click(row);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("regulation helpers", () => {
  const clauses = [
    { number: "3.4", label: "п. 3.4", text: "Справка содержит дату выдачи", section: ["Справка об обучении"] },
    { number: "3.5", label: "п. 3.5", text: "Справка выдаётся бесплатно", section: ["Справка об обучении"] },
    { number: "6.1", label: "п. 6.1", text: "Срок рассмотрения 5 дней", section: ["Сроки"] },
  ];
  it("filters, highlights and groups", () => {
    expect(filterClauses(clauses, "дату")).toHaveLength(1);
    expect(filterClauses(clauses, "сроки")[0]!.number).toBe("6.1");
    expect(highlightText("Справка и справка", "справк")).toEqual([{ hit: "Справк" }, "а и ", { hit: "справк" }, "а"]);
    const sections = groupSections(clauses);
    expect(sections.map((s) => [s.title, s.clauses.length])).toEqual([
      ["Справка об обучении", 2],
      ["Сроки", 1],
    ]);
  });
  it("shows documents of the knowledge base as their own sections with human numbers, not internal keys", () => {
    const all = [
      ...clauses,
      { number: "db490d4:2.1", display: "2.1", label: "п. 2.1 (Правила общежития)", text: "Плата за проживание 1500 рублей", section: ["Проживание"], document: { id: "db490d4", name: "Правила общежития" } },
      { number: "d7eadb1:§1", display: "§1", label: "§1 (Библиотека)", text: "Заниматься можно до 21:00", section: [], document: { id: "d7eadb1", name: "Библиотека" } },
    ];
    expect(groupSections(all).map((s) => [s.title, s.clauses.length])).toEqual([["Справка об обучении", 2], ["Сроки", 1], ["Документ «Правила общежития»", 1], ["Документ «Библиотека»", 1]]);
    expect(clauseNo(all[3]!)).toBe("2.1");
    expect(clauseNo(all[2]!)).toBe("6.1");
    expect(filterClauses(all, "общежития")).toHaveLength(1); // поиск знает и название документа
    expect(filterClauses(all, "§1")).toHaveLength(1);
    expect(filterClauses(all, "db490d4")).toHaveLength(0); // служебный ключ людям не нужен
  });
  it("parses the hash route", () => {
    expect(parseHash("")).toEqual({ route: "check", clause: null });
    expect(parseHash("#/types")).toEqual({ route: "check", clause: null }); // раздела «Типы документов» больше нет
    expect(parseHash("#/rules/3.4")).toEqual({ route: "rules", clause: "3.4" });
  });
});

describe("useDesk", () => {
  afterEach(() => vi.restoreAllMocks());

  it("adds a turn, tracks the job and stores the result", async () => {
    const result = { answer: { verdict: "ok", text: "ok" }, fields: [], images: [], warnings: [] };
    let polls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      const body = String(url) === "/api/jobs" ? { id: "j1" } : ++polls < 2 ? { id: "j1", status: "running", stage: "ocr", label: "Читаю", fraction: 0.4, elapsed: 1 } : { id: "j1", status: "done", stage: "done", label: "Готово", fraction: 1, elapsed: 2, result };
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    });
    const { result: hook } = renderHook(() => useDesk());
    let promise: Promise<unknown> = Promise.resolve();
    act(() => {
      promise = hook.current.run({ question: " Вопрос ", docType: "auto" });
    });
    expect(hook.current.turns).toHaveLength(1);
    expect(hook.current.turns[0]).toMatchObject({ phase: "running", question: "Вопрос" });
    expect(hook.current.busy).toBe(true);
    await act(async () => {
      await promise;
    });
    await waitFor(() => expect(hook.current.turns[0]!.phase).toBe("done"), { timeout: 4000 });
    expect(hook.current.turns[0]!.result).toEqual(result);
    expect(hook.current.busy).toBe(false);
  });

  it("marks a turn as failed with the server's message", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/jobs") return new Response(JSON.stringify({ detail: "Файл пустой" }), { status: 400, headers: { "Content-Type": "application/json" } });
      return new Response("{}", { status: 404 });
    });
    const { result: hook } = renderHook(() => useDesk());
    await act(async () => {
      await hook.current.run({ question: "?", docType: "auto" });
    });
    expect(hook.current.turns[0]).toMatchObject({ phase: "error", error: "Файл пустой" });
  });
});
