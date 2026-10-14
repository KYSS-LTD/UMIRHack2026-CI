import { api, ApiError, runAnalysis } from "../api";
import type { JobSnapshot } from "../types";

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const snap = (over: Partial<JobSnapshot>): JobSnapshot => ({ id: "j1", status: "running", stage: "ocr", label: "Читаю текст", fraction: 0.3, elapsed: 1, ...over });

afterEach(() => vi.restoreAllMocks());

describe("runAnalysis", () => {
  it("polls the job until it is done and reports progress", async () => {
    const result = { answer: { verdict: "ok", text: "ok" } };
    let polls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => {
      if (String(url) === "/api/jobs") return json({ id: "j1" });
      polls++;
      return polls < 2 ? json(snap({})) : json(snap({ status: "done", stage: "done", fraction: 1, result: result as never }));
    });
    const seen: number[] = [];
    const out = await runAnalysis({ question: "?", docType: "auto" }, { onProgress: (s) => seen.push(s.fraction) }, new AbortController().signal, 1);
    expect(out).toEqual(result);
    expect(seen).toEqual([0.3, 1]);
  });

  it("turns a failed job into an ApiError with the server's message", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) =>
      String(url) === "/api/jobs" ? json({ id: "j1" }) : json(snap({ status: "error", error: "Не удалось прочитать файл" })),
    );
    await expect(runAnalysis({ question: "?", docType: "auto" }, { onProgress: () => {} }, new AbortController().signal, 1)).rejects.toThrow("Не удалось прочитать файл");
  });

  it("stops quietly when aborted", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => (String(url) === "/api/jobs" ? json({ id: "j1" }) : json(snap({}))));
    const ctrl = new AbortController();
    const p = runAnalysis({ question: "?", docType: "auto" }, { onProgress: () => ctrl.abort() }, ctrl.signal, 1);
    await expect(p).rejects.toMatchObject({ name: "AbortError" });
  });

  it("treats a cancelled job as an abort, not an error", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (url) => (String(url) === "/api/jobs" ? json({ id: "j1" }) : json(snap({ status: "cancelled" }))));
    await expect(runAnalysis({ question: "?", docType: "auto" }, { onProgress: () => {} }, new AbortController().signal, 1)).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("api errors", () => {
  it("reads FastAPI detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Файл пустой" }, 400));
    await expect(api.health()).rejects.toMatchObject({ message: "Файл пустой", status: 400 });
  });
  it("does not show FastAPI's English 'Not Found' but says the server is probably out of date", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Not Found" }, 404));
    const err = await api.health().catch((e) => e);
    expect(err.status).toBe(404);
    expect(err.message).toMatch(/запущен со старой версией/);
    expect(err.message).not.toMatch(/Not Found/);
    vi.spyOn(globalThis, "fetch").mockResolvedValue(json({ detail: "Обращение не найдено" }, 404));
    await expect(api.health()).rejects.toMatchObject({ message: "Обращение не найдено" }); // свои тексты сервера не затрагиваются
  });
  it("explains a dead server", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("Failed to fetch"));
    const err = await api.health().catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toMatch(/Нет связи/);
  });
});
