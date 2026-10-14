import { fireEvent, render, screen } from "@testing-library/react";
import { OcrTextPanel, splitMatches } from "../components/desk/OcrTextPanel";
import { PaperViewer } from "../components/desk/PaperViewer";
import { ToastProvider } from "../ui/Toasts";

const TEXT = "ДОГОВОР № 12\nПредмет договора: оказание услуг.\nСтороны договора обязуются соблюдать условия.";

describe("splitMatches", () => {
  it("marks every match, ignoring case and ё", () => {
    const parts = splitMatches("Ёлка и ёлки, Елена", "елк");
    expect(parts.filter((p) => p.hit).map((p) => p.text)).toEqual(["Ёлк", "ёлк"]);
    expect(parts.map((p) => p.text).join("")).toBe("Ёлка и ёлки, Елена"); // текст не теряется
  });

  it("returns the text as is when nothing is searched or found", () => {
    expect(splitMatches("abc", "")).toEqual([{ text: "abc", hit: false }]);
    expect(splitMatches("abc", "  ")).toEqual([{ text: "abc", hit: false }]);
    expect(splitMatches("abc", "x")).toEqual([{ text: "abc", hit: false }]);
  });
});

describe("OcrTextPanel", () => {
  const panel = (over: Partial<Parameters<typeof OcrTextPanel>[0]> = {}) =>
    render(
      <ToastProvider>
        <OcrTextPanel text={TEXT} fileName="scan.jpg" onClose={() => {}} {...over} />
      </ToastProvider>,
    );

  it("shows the text with its size", () => {
    const { container } = panel();
    expect(container.querySelector(".text-body")!.textContent).toBe(TEXT);
    expect(container.querySelector("header span")!.textContent).toMatch(/^12 слов · \d+ зн\.$/);
  });

  it("counts and marks matches while typing", () => {
    const { container } = panel();
    const input = screen.getByRole("searchbox", { name: "Найти в распознанном тексте" });
    fireEvent.change(input, { target: { value: "договора" } });
    expect(container.querySelectorAll(".text-body mark")).toHaveLength(2);
    expect(container.querySelector(".text-count")!.textContent).toBe("2 совпадения");
    fireEvent.change(input, { target: { value: "кредит" } });
    expect(container.querySelector(".text-count")!.textContent).toBe("нет совпадений");
    expect(container.querySelectorAll(".text-body mark")).toHaveLength(0);
  });

  it("asks to show the phrase on the sheet only for a non-empty phrase", () => {
    const onFind = vi.fn();
    panel({ onFind });
    const show = screen.getByRole("button", { name: "На снимке" });
    expect(show).toBeDisabled();
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "  предмет договора " } });
    expect(show).toBeEnabled();
    fireEvent.click(show);
    expect(onFind).toHaveBeenCalledWith("предмет договора");
  });

  it("has no «На снимке» button when it cannot ask", () => {
    panel();
    expect(screen.queryByRole("button", { name: "На снимке" })).toBeNull();
  });

  it("is closed by the button and by Esc in the empty search field", () => {
    const onClose = vi.fn();
    panel({ onClose });
    fireEvent.click(screen.getByRole("button", { name: "Закрыть текст" }));
    fireEvent.keyDown(screen.getByRole("searchbox"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("Esc first clears a typed phrase and does not close the panel", () => {
    const onClose = vi.fn();
    panel({ onClose });
    const input = screen.getByRole("searchbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "дог" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(input.value).toBe("");
    expect(onClose).not.toHaveBeenCalled();
  });

  it("copies the text and downloads it as a .txt named after the file", () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const created: Blob[] = [];
    URL.createObjectURL = vi.fn((b: Blob | MediaSource) => (created.push(b as Blob), "blob:t"));
    URL.revokeObjectURL = vi.fn();
    const names: string[] = [];
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
      names.push(this.download);
    });
    panel();
    fireEvent.click(screen.getByRole("button", { name: /Копировать/ }));
    expect(writeText).toHaveBeenCalledWith(TEXT);
    fireEvent.click(screen.getByRole("button", { name: /Скачать/ }));
    expect(names).toEqual(["scan-текст.txt"]);
    expect(created[0]!.type).toBe("text/plain;charset=utf-8");
    click.mockRestore();
  });
});

describe("«Текст» in the viewer toolbar", () => {
  const props = {
    src: "data:image/jpeg;base64,AAAA",
    pages: null,
    fileName: "scan.jpg",
    pdf: false,
    scanning: false,
    progress: 0,
    stageLabel: "",
    highlight: null,
    hover: null,
    allBoxes: [],
    showAll: false,
    revealKey: "t1:done",
    onToggleAll: () => {},
    onClear: () => {},
    docs: [],
    activeDoc: 0,
    busy: false,
    onSelectDoc: () => {},
    onAnother: () => {},
  };
  const view = (over: Record<string, unknown> = {}) => (
    <ToastProvider>
      <PaperViewer {...props} {...over} />
    </ToastProvider>
  );

  it("has no button while there is no text", () => {
    render(view());
    expect(screen.queryByRole("button", { name: /Текст/ })).toBeNull();
  });

  it("opens and closes the panel, and forgets it when another document is laid on the desk", () => {
    const { container, rerender } = render(view({ text: TEXT }));
    const button = screen.getByRole("button", { name: /Текст/ });
    expect(container.querySelector(".text-panel")).toBeNull();
    fireEvent.click(button);
    expect(container.querySelector(".text-panel")).not.toBeNull();
    expect(button).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(button);
    expect(container.querySelector(".text-panel")).toBeNull();
    fireEvent.click(button);
    rerender(view({ text: "Другой текст", fileName: "other.jpg" }));
    expect(container.querySelector(".text-panel")).toBeNull();
  });

  it("closes the panel when the phrase is sent to the sheet, so the highlight is visible", () => {
    const onFindText = vi.fn();
    const { container } = render(view({ text: TEXT, onFindText }));
    fireEvent.click(screen.getByRole("button", { name: /Текст/ }));
    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "услуг" } });
    fireEvent.click(screen.getByRole("button", { name: "На снимке" }));
    expect(onFindText).toHaveBeenCalledWith("услуг");
    expect(container.querySelector(".text-panel")).toBeNull();
  });
});
