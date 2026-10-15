import { render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { Composer } from "../components/desk/Composer";
import { PhotoNotice } from "../components/desk/PhotoNotice";
import { usePhotoCheck } from "../hooks/usePhotoCheck";
import { suggestionsFor } from "../lib/suggestions";
import type { DocTypeSummary, PhotoQuality } from "../types";

const noop = () => {};
const blur = { code: "blur", severity: "bad" as const, title: "Снимок размытый", hint: "Держите телефон неподвижно, дождитесь фокуса и снимите ещё раз." };
const dark = { code: "dark", severity: "warn" as const, title: "Снимок тёмный", hint: "Включите свет или снимайте у окна." };
const poor: PhotoQuality = { level: "poor", retake: true, issues: [blur, dark] };
const fair: PhotoQuality = { level: "fair", retake: false, issues: [dark] };
const good: PhotoQuality = { level: "good", retake: false, issues: [] };
const photo = (name = "a.jpg", type = "image/jpeg") => new File(["x"], name, { type });

describe("PhotoNotice", () => {
  it("says what is wrong and how to shoot better, and offers a new file when a retake is advised", async () => {
    const onRetake = vi.fn();
    render(<PhotoNotice quality={poor} onRetake={onRetake} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Снимок лучше переснять");
    expect(screen.getByText(/Держите телефон неподвижно/)).toBeInTheDocument();
    expect(screen.getByText(/Включите свет/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Загрузить другой снимок/ }));
    expect(onRetake).toHaveBeenCalledTimes(1);
  });

  it("is a calm hint, without a button, when the photo is only so-so", () => {
    render(<PhotoNotice quality={fair} onRetake={noop} />);
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("note")).toHaveTextContent("Снимок можно улучшить");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("is one line in compact mode and shows nothing without real issues", () => {
    const { container, rerender } = render(<PhotoNotice quality={poor} compact />);
    expect(screen.getAllByRole("listitem")).toHaveLength(1);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    rerender(<PhotoNotice quality={{ level: "good", retake: false, issues: [{ ...dark, severity: "info" }] }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("usePhotoCheck", () => {
  afterEach(() => vi.restoreAllMocks());

  it("warns about a photo that is worth retaking, as soon as the file is picked", async () => {
    const spy = vi.spyOn(api, "photoQuality").mockResolvedValue(poor);
    const file = photo();
    const { result } = renderHook(() => usePhotoCheck(file));
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current?.retake).toBe(true));
    expect(spy).toHaveBeenCalledTimes(1);
  });

  it("stays quiet for a fine photo, for a PDF and when the check fails", async () => {
    const spy = vi.spyOn(api, "photoQuality").mockResolvedValueOnce(good).mockRejectedValueOnce(new Error("нет сети"));
    const okFile = photo("ok.jpg");
    const brokenFile = photo("x.jpg");
    const pdfFile = photo("scan.pdf", "application/pdf");
    const ok = renderHook(() => usePhotoCheck(okFile));
    const broken = renderHook(() => usePhotoCheck(brokenFile));
    renderHook(() => usePhotoCheck(pdfFile));
    renderHook(() => usePhotoCheck(null));
    await new Promise((r) => setTimeout(r, 20));
    expect(ok.result.current).toBeNull();
    expect(broken.result.current).toBeNull();
    expect(spy).toHaveBeenCalledTimes(2); // PDF и пустой выбор сервер не спрашивают
  });

  it("never shows the advice about a previous file", async () => {
    vi.spyOn(api, "photoQuality").mockResolvedValueOnce(poor).mockResolvedValueOnce(good);
    const first = photo("first.jpg");
    const second = photo("second.jpg");
    const { result, rerender } = renderHook(({ f }) => usePhotoCheck(f), { initialProps: { f: first } });
    await waitFor(() => expect(result.current?.retake).toBe(true));
    rerender({ f: second });
    expect(result.current).toBeNull();
  });
});

describe("Composer on an empty desk", () => {
  const types: DocTypeSummary[] = [{ id: "study_certificate", title: "Справка об обучении", short_title: "Справка", source: "", fields: [], examples: [], suggestions: [] }];
  const base = {
    file: null,
    question: "",
    docType: "auto",
    types,
    suggestions: suggestionsFor(types, "auto", null, []),
    maxMb: 25,
    busy: false,
    onFile: noop,
    onQuestion: noop,
    onDocType: noop,
    onSubmit: noop,
    onCancel: noop,
    onError: noop,
  };

  afterEach(() => vi.restoreAllMocks());

  it("shows only the field and the send button: no suggestion chips, no type picker", () => {
    render(<Composer {...base} idle />);
    expect(screen.queryByRole("group", { name: "Подсказки вопросов" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Тип документа/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Вопрос")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Проверить" })).toBeInTheDocument();
  });

  it("brings the chips and the type picker back once there is a document", () => {
    vi.spyOn(api, "photoQuality").mockResolvedValue(good);
    render(<Composer {...base} file={photo()} />);
    expect(screen.getByRole("group", { name: "Подсказки вопросов" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Тип документа/ })).toBeInTheDocument();
  });

  it("warns under the chosen file that the photo is poor, without blocking the check", async () => {
    vi.spyOn(api, "photoQuality").mockResolvedValue(poor);
    render(<Composer {...base} file={photo("blur.jpg")} question="Хватит?" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("Снимок размытый");
    expect(screen.getByRole("button", { name: "Проверить" })).toBeEnabled();
  });
});
