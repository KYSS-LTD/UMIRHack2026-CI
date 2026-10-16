import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { NavBar } from "../components/NavBar";
import { placeBubble, Tour, tourSteps } from "../components/Tour";
import { AccountProvider } from "../hooks/useAccount";
import { emit, useBus } from "../lib/bus";
import type { SampleItem } from "../types";
import { ToastProvider } from "../ui/Toasts";
import { CheckView } from "../views/CheckView";

const noop = () => {};
const view = { w: 1280, h: 720 };
const bubble = { w: 340, h: 190 };

describe("placeBubble", () => {
  it("puts the card below the element and draws the arrow up to it", () => {
    const target = { left: 500, top: 100, width: 200, height: 50 };
    const p = placeBubble(target, bubble, view);
    expect(p.side).toBe("bottom");
    expect(p.top).toBeGreaterThan(150); // ниже нижнего края элемента
    expect(p.left).toBeGreaterThanOrEqual(16);
    expect(p.path).toMatch(/^M[\d.]+ [\d.]+ C/);
    expect(p.head.startsWith(`M${(600).toFixed(1)} ${(154).toFixed(1)}`)).toBe(true); // вершина наконечника — у нижнего края элемента, по его центру
  });

  it("goes above when there is no room below, and to the side when there is none above or below", () => {
    expect(placeBubble({ left: 500, top: 640, width: 200, height: 50 }, bubble, view).side).toBe("top");
    const tall = placeBubble({ left: 20, top: 20, width: 120, height: 680 }, bubble, view);
    expect(tall.side).toBe("right");
    expect(tall.left).toBeGreaterThan(140);
    expect(placeBubble({ left: 1100, top: 20, width: 160, height: 680 }, bubble, view).side).toBe("left");
  });

  it("never lets the card leave the window", () => {
    for (const target of [{ left: -40, top: 5, width: 60, height: 40 }, { left: 1250, top: 690, width: 60, height: 40 }, { left: 0, top: 0, width: 1280, height: 720 }]) {
      const p = placeBubble(target, bubble, view);
      expect(p.left).toBeGreaterThanOrEqual(16);
      expect(p.top).toBeGreaterThanOrEqual(16);
      expect(p.left + bubble.w).toBeLessThanOrEqual(view.w - 16);
      expect(p.top + bubble.h).toBeLessThanOrEqual(view.h - 16);
    }
  });
});

describe("tourSteps", () => {
  const withDoc = (verdict: string, editable = false) => {
    const root = document.createElement("div");
    root.innerHTML = `<section class="viewer"></section><section class="verdict" data-verdict="${verdict}"></section>${editable ? '<button class="f-fix"></button>' : ""}`;
    return root;
  };
  const demo: SampleItem = { file: "sample_02.png", title: "Справка: чего-то не хватает", question: "Чего не хватает?", doc_type: "study_certificate" } as SampleItem;

  it("shows the steps of an empty desk, or of a desk with a document when one is open", () => {
    const empty = document.createElement("div");
    expect(tourSteps("check", empty).map((s) => s.target)).toContain(".tray .tray-actions");
    const targets = tourSteps("check", withDoc("ok")).map((s) => s.target);
    expect(targets).toContain(".verdict");
    expect(targets).not.toContain(".tray .tray-actions");
    expect(tourSteps("rules", empty).map((s) => s.target)).toContain(".reg .search");
    expect(tourSteps("status", empty).map((s) => s.target)).toContain(".status-form");
    expect(tourSteps("account", empty).map((s) => s.target)).toEqual([".account-view .btn.primary", ".nav .switch"]); // вход не выполнен: зовём войти
    const signedIn = document.createElement("div");
    signedIn.innerHTML = '<section class="acc-card"></section>';
    expect(tourSteps("account", signedIn).map((s) => s.target)).toEqual([".acc-card", ".nav .switch"]);
  });

  it("keeps only the main things: at most five steps on any screen, each with a distinct id", () => {
    for (const route of ["check", "rules", "status", "account"] as const) {
      for (const root of [document.createElement("div"), withDoc("ok"), withDoc("missing", true)]) {
        const list = tourSteps(route, root, { demo });
        expect(list.length).toBeGreaterThan(0);
        expect(list.length).toBeLessThanOrEqual(5);
        expect(new Set(list.map((x) => x.id)).size).toBe(list.length);
      }
    }
  });

  it("says what to do next depending on the verdict, not just what the frame is", () => {
    const pick = (verdict: string) => tourSteps("check", withDoc(verdict))[0]!;
    expect(pick("missing").title).toBe("Чего не хватает");
    expect(pick("missing").actions?.map((a) => a.event)).toEqual(["pick-file"]);
    expect(pick("ok").actions?.map((a) => a.event)).toEqual(["say"]);
    expect(pick("no_answer").title).toBe("Ответа нет");
    expect(pick("no_answer").actions?.map((a) => a.event)).toEqual(["focus-question"]);
    expect(tourSteps("check", withDoc("что-то-новое"))[0]!.title).toBe("Ответа нет"); // неизвестный вердикт — как «ответа нет», а не пустая карточка
  });

  it("mentions fixing a field only when the card can be edited", () => {
    const fields = (editable: boolean) => tourSteps("check", withDoc("ok", editable)).find((s) => s.id === "fields")!;
    expect(fields(true).text).toMatch(/карандаш/);
    expect(fields(false).text).not.toMatch(/карандаш/);
  });

  it("offers to try the example only when there is one, and to ask follow-ups on a document", () => {
    const empty = document.createElement("div");
    expect(tourSteps("check", empty, { demo })[0]!.actions).toEqual([{ label: "Попробовать на примере", event: "sample", detail: demo }]);
    expect(tourSteps("check", empty)[0]!.actions).toBeUndefined();
    const follow = tourSteps("check", withDoc("ok")).find((s) => s.id === "followup")!;
    expect(follow.actions?.every((a) => a.event === "ask" && a.detail === a.label)).toBe(true);
    expect(follow.actions?.map((a) => a.label).join(" ")).toMatch(/Положению/); // про правила при загруженном документе — явно «по Положению»
  });
});

describe("Tour", () => {
  const demo = { file: "sample_02.png", title: "Справка: не хватает печати", question: "", doc_type: "auto" } as SampleItem;
  const Page = ({ open = true, onClose = noop, hasSwitch = true, withDemo = false }: { open?: boolean; onClose?: () => void; hasSwitch?: boolean; withDemo?: boolean }) => (
    <>
      <div className="nav">{hasSwitch && <nav className="switch" />}</div>
      <div className="tray">
        <div className="tray-actions" />
      </div>
      <div className="dock">
        <div className="dock-field" />
      </div>
      <Tour open={open} route="check" onClose={onClose} demo={withDemo ? demo : null} isVisible={() => true} />
    </>
  );

  it("starts with the first visible step, with a counter, text and an arrow", () => {
    const { container } = render(<Page />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Положите документ" })).toBeInTheDocument();
    expect(screen.getByText(/Подсказка 1 из 4/)).toBeInTheDocument(); // файл, вопрос, заявление, «Положение»
    expect(document.querySelector(".tour-arrow path")).not.toBeNull();
    expect(document.querySelector(".tour-spot")).not.toBeNull();
    expect(container).toBeTruthy();
  });

  it("skips steps whose element is not on the screen", () => {
    render(<Page hasSwitch={false} />);
    expect(screen.getByText(/Подсказка 1 из 3/)).toBeInTheDocument();
  });

  it("walks forward and back, and finishes with «Готово»", async () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} />);
    expect(screen.getByRole("button", { name: /Назад/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Далее/ }));
    expect(screen.getByRole("heading", { name: "Спросите по делу" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    expect(screen.getByRole("heading", { name: "Положите документ" })).toBeInTheDocument();
    for (let i = 0; i < 4; i++) await userEvent.click(screen.getByRole("button", { name: /Далее|Готово/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("is controlled from the keyboard: arrows move, Esc closes", () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} />);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("heading", { name: "Спросите по делу" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "ArrowLeft" });
    expect(screen.getByRole("heading", { name: "Положите документ" })).toBeInTheDocument();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("can be skipped at any step and renders nothing while closed", async () => {
    const onClose = vi.fn();
    const { rerender } = render(<Page onClose={onClose} />);
    await userEvent.click(screen.getByRole("button", { name: "Пропустить" }));
    expect(onClose).toHaveBeenCalledTimes(1);
    rerender(<Page open={false} onClose={onClose} />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("runs a step action: closes the tour and sends the event the desk listens to", async () => {
    const onClose = vi.fn();
    const heard = vi.fn();
    const Probe = () => (useBus<string>("ask", heard), null);
    render(
      <>
        <Probe />
        <Page onClose={onClose} />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: /Далее/ })); // «Спросите по делу»
    await userEvent.click(screen.getByRole("button", { name: /Сколько дней рассматривается заявление/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(heard).toHaveBeenCalledWith("Сколько дней рассматривается заявление?");
  });

  it("offers the example on the first step when examples exist", async () => {
    const onClose = vi.fn();
    const sample = vi.fn();
    const Probe = () => (useBus<SampleItem>("sample", sample), null);
    render(
      <>
        <Probe />
        <Page onClose={onClose} withDemo />
      </>,
    );
    await userEvent.click(screen.getByRole("button", { name: /Попробовать на примере/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(sample).toHaveBeenCalledWith(demo);
  });

  it("shows no action buttons on steps that only explain", async () => {
    render(<Page />);
    expect(screen.queryByRole("group", { name: "Попробовать сразу" })).not.toBeInTheDocument(); // без примера у первого шага кнопок нет
    for (let i = 0; i < 3; i++) await userEvent.click(screen.getByRole("button", { name: /Далее/ }));
    expect(screen.getByRole("heading", { name: "Положение под рукой" })).toBeInTheDocument();
    expect(screen.queryByRole("group", { name: "Попробовать сразу" })).not.toBeInTheDocument();
  });

  it("says so, instead of an empty frame, when there is nothing to point at", () => {
    render(<Tour open route="check" onClose={noop} isVisible={() => true} />);
    expect(screen.getByText(/нечего показывать/)).toBeInTheDocument();
  });
});

describe("tips on the desk", () => {
  afterEach(() => vi.restoreAllMocks());

  it("asks a question from a tip like a typed one: the desk starts a check with that text", async () => {
    vi.spyOn(api, "ruleSuggestions").mockResolvedValue({ rules: [] });
    vi.spyOn(api.assistant, "state").mockRejectedValue(new Error("нет сессии")); // без сессии анкеты вопрос идёт прямой проверкой
    vi.spyOn(api.account, "me").mockResolvedValue({ user: null, telegram: { enabled: false, login: false, bot: "", mini_app: false } });
    const start = vi.spyOn(api, "startJob").mockRejectedValue(new Error("стоп"));
    render(
      <ToastProvider>
        <AccountProvider>
          <CheckView types={[]} samples={[]} health={null} onOpenClause={noop} />
        </AccountProvider>
      </ToastProvider>,
    );
    act(() => emit("ask", "Сколько дней рассматривается заявление?"));
    await waitFor(() => expect(start).toHaveBeenCalledTimes(1));
    expect(start.mock.calls[0]![0]).toMatchObject({ question: "Сколько дней рассматривается заявление?", docType: "auto", file: null });
  });
});

describe("the «?» button in the navigation bar", () => {
  it("opens the tour and carries a dot until the tour has been used", async () => {
    const onTour = vi.fn();
    const { rerender } = render(<NavBar route="check" onRoute={noop} theme="system" onThemeSet={noop} onPalette={noop} onHelp={noop} onTour={onTour} tourNew />);
    const button = screen.getByRole("button", { name: "Подсказки: как пользоваться" });
    expect(button).toHaveAttribute("data-new");
    await userEvent.click(button);
    expect(onTour).toHaveBeenCalledTimes(1);
    rerender(<NavBar route="check" onRoute={noop} theme="system" onThemeSet={noop} onPalette={noop} onHelp={noop} onTour={onTour} />);
    expect(screen.getByRole("button", { name: "Подсказки: как пользоваться" })).not.toHaveAttribute("data-new");
  });

  it("is absent when no handler is given", () => {
    render(<NavBar route="check" onRoute={noop} theme="system" onThemeSet={noop} onPalette={noop} onHelp={noop} />);
    expect(screen.queryByRole("button", { name: "Подсказки: как пользоваться" })).not.toBeInTheDocument();
  });
});
