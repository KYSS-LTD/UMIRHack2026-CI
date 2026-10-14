import { render, renderHook, waitFor } from "@testing-library/react";
import { api } from "../api";
import { PaperViewer } from "../components/desk/PaperViewer";
import { usePagePreview } from "../hooks/usePagePreview";
import type { PagePreview } from "../types";
import { ToastProvider } from "../ui/Toasts";

const page = (over: Partial<PagePreview> = {}): PagePreview => ({ page: 0, width: 1200, height: 1700, data_url: "data:image/jpeg;base64,AAAA", ...over });
const pdf = () => new File(["%PDF-1.4"], "scan.pdf", { type: "application/pdf" });

describe("usePagePreview", () => {
  afterEach(() => vi.restoreAllMocks());

  it("loads the real first page of a PDF", async () => {
    const spy = vi.spyOn(api, "previewPage").mockResolvedValue(page());
    const file = pdf();
    const { result } = renderHook(() => usePagePreview(file, "scan.pdf"));
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current?.data_url).toBe("data:image/jpeg;base64,AAAA"));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("does not ask the server for images: they already have a local preview", () => {
    const spy = vi.spyOn(api, "previewPage").mockResolvedValue(page());
    renderHook(() => usePagePreview(new File(["x"], "a.png", { type: "image/png" }), "a.png"));
    renderHook(() => usePagePreview(new File(["x"], "a.jpg", { type: "image/jpeg" }), "a.jpg"));
    renderHook(() => usePagePreview(null));
    expect(spy).not.toHaveBeenCalled();
  });

  it("never shows the page of a previous file and survives a failed preview", async () => {
    const first = pdf();
    const second = new File(["%PDF-1.4"], "other.pdf", { type: "application/pdf" });
    vi.spyOn(api, "previewPage").mockResolvedValueOnce(page({ data_url: "data:first" })).mockRejectedValueOnce(new Error("нет сети"));
    const { result, rerender } = renderHook(({ f }) => usePagePreview(f, f.name), { initialProps: { f: first } });
    await waitFor(() => expect(result.current?.data_url).toBe("data:first"));
    rerender({ f: second });
    expect(result.current).toBeNull(); // чужой лист не показывается, пока нет своего (и если предпросмотр не получился — остаётся заглушка)
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current).toBeNull();
  });
});

describe("PaperViewer keeps the sheet on the desk", () => {
  const base = {
    src: null as string | null,
    pages: null as PagePreview[] | null,
    fileName: "scan.pdf",
    pdf: true,
    scanning: false,
    progress: 0,
    stageLabel: "",
    highlight: null,
    hover: null,
    allBoxes: [],
    showAll: false,
    revealKey: "t1:running",
    onToggleAll: () => {},
    onClear: () => {},
    docs: [],
    activeDoc: 0,
    busy: false,
    onSelectDoc: () => {},
    onAnother: () => {},
  };
  const view = (over: Partial<typeof base> & { srcSize?: { w: number; h: number } | null }) => (
    <ToastProvider>
      <PaperViewer {...base} {...over} />
    </ToastProvider>
  );

  it("shows the PDF placeholder only until the real page arrives, then swaps without a fade", () => {
    const { container, rerender } = render(view({ scanning: true }));
    expect(container.querySelector(".pdf-sheet")).not.toBeNull();
    rerender(view({ scanning: true, src: "data:image/jpeg;base64,AAAA", srcSize: { w: 1200, h: 1700 } }));
    const img = container.querySelector(".canvas img");
    expect(container.querySelector(".pdf-sheet")).toBeNull();
    expect(img).not.toBeNull();
    expect(img).toHaveClass("settled"); // пришло на место заглушки — ложится без «проявления», лист не мигает
    expect(container.querySelector(".beam")).not.toBeNull(); // сканирование идёт по настоящему листу
  });

  it("does not replace the page with the placeholder or restart its fade when the same file is checked again", () => {
    const { container, rerender } = render(view({ src: "data:image/jpeg;base64,AAAA", srcSize: { w: 1200, h: 1700 } }));
    const first = container.querySelector(".canvas img");
    rerender(view({ scanning: true, pages: [page({ data_url: "data:image/jpeg;base64,BBBB" })] })); // прошлый результат остаётся на столе во время повторной проверки
    rerender(view({ pages: [page({ data_url: "data:image/jpeg;base64,CCCC" })] })); // пришёл новый результат
    expect(container.querySelector(".pdf-sheet")).toBeNull();
    expect(container.querySelector(".canvas img")).toBe(first); // тот же элемент: анимация появления не запускается заново
    expect(container.querySelector(".canvas img")).toHaveAttribute("src", "data:image/jpeg;base64,CCCC");
  });

  it("a different document is laid on the desk with a fresh fade", () => {
    const { container, rerender } = render(view({ src: "data:image/jpeg;base64,AAAA" }));
    const first = container.querySelector(".canvas img");
    rerender(view({ src: "data:image/jpeg;base64,ZZZZ", fileName: "another.pdf" }));
    expect(container.querySelector(".canvas img")).not.toBe(first);
    expect(container.querySelector(".canvas img")).not.toHaveClass("settled");
  });
});
