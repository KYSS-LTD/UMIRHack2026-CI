import { cropAround } from "../components/desk/highlight";
import { validateFile } from "../lib/files";
import { formatBytes, pct, relativeTime, unionBoxes } from "../lib/format";
import { rank, score } from "../lib/fuzzy";
import { HISTORY_LIMIT, loadHistory, type HistoryEntry, pushEntry } from "../lib/history";
import { STAGES, stageIndex } from "../lib/stages";
import { suggestionsFor } from "../lib/suggestions";
import type { DocTypeSummary } from "../types";

const entry = (over: Partial<HistoryEntry> = {}): HistoryEntry => ({
  id: String(Math.random()),
  at: 1,
  question: "Q",
  fileName: "a.png",
  docType: "auto",
  typeTitle: "Справка",
  verdict: "ok",
  answer: "A",
  ...over,
});

describe("format", () => {
  it("formats sizes", () => {
    expect(formatBytes(500)).toBe("500 Б");
    expect(formatBytes(2048)).toBe("2.0 КБ");
    expect(formatBytes(5 * 1024 * 1024)).toBe("5.0 МБ");
  });
  it("clamps percentages", () => {
    expect(pct(0.456)).toBe(46);
    expect(pct(3)).toBe(100);
    expect(pct(-1)).toBe(0);
  });
  it("describes recent time", () => {
    const now = Date.now();
    expect(relativeTime(now - 10_000, now)).toBe("только что");
    expect(relativeTime(now - 5 * 60_000, now)).toBe("5 мин назад");
  });
  it("unions boxes", () => {
    expect(unionBoxes([])).toBeNull();
    expect(
      unionBoxes([
        { page: 0, x0: 10, y0: 20, x1: 30, y1: 40 },
        { page: 0, x0: 5, y0: 25, x1: 50, y1: 35 },
      ]),
    ).toEqual({ page: 0, x0: 5, y0: 20, x1: 50, y1: 40 });
  });
});

describe("validateFile", () => {
  it("accepts images and pdf", () => {
    expect(validateFile({ name: "a.jpg", type: "image/jpeg", size: 100 }, 25)).toBeNull();
    expect(validateFile({ name: "a.PDF", type: "", size: 100 }, 25)).toBeNull();
  });
  it("rejects other types, empty and huge files", () => {
    expect(validateFile({ name: "a.exe", type: "application/octet-stream", size: 100 }, 25)).toMatch(/JPG/);
    // форматы — ровно JPG, JPEG, PNG и PDF
    expect(validateFile({ name: "a.webp", type: "image/webp", size: 100 }, 25)).toMatch(/JPEG, PNG или PDF/);
    expect(validateFile({ name: "a.heic", type: "image/heic", size: 100 }, 25)).toMatch(/JPG/);
    expect(validateFile({ name: "a.jpeg", type: "", size: 100 }, 25)).toBeNull();
    expect(validateFile({ name: "a.png", type: "image/png", size: 100 }, 25)).toBeNull();
    expect(validateFile({ name: "a.png", type: "image/png", size: 0 }, 25)).toMatch(/пустой/);
    expect(validateFile({ name: "a.png", type: "image/png", size: 30 * 1024 * 1024 }, 25)).toMatch(/25 МБ/);
  });
});

describe("history", () => {
  it("puts the newest first and replaces duplicates", () => {
    const list = pushEntry(pushEntry([], entry({ id: "1" })), entry({ id: "2", question: "Other" }));
    expect(list.map((e) => e.id)).toEqual(["2", "1"]);
    expect(pushEntry(list, entry({ id: "3" })).map((e) => e.id)).toEqual(["3", "2"]);
  });
  it("keeps the limit", () => {
    let list: HistoryEntry[] = [];
    for (let i = 0; i < HISTORY_LIMIT + 10; i++) list = pushEntry(list, entry({ id: String(i), question: `q${i}` }));
    expect(list).toHaveLength(HISTORY_LIMIT);
  });
  it("survives broken storage", () => {
    expect(loadHistory({ getItem: () => "{not json" })).toEqual([]);
    expect(loadHistory({ getItem: () => JSON.stringify([{ nope: 1 }, entry({ id: "ok" })]) }).map((e) => e.id)).toEqual(["ok"]);
    expect(
      loadHistory({
        getItem: () => {
          throw new Error("denied");
        },
      }),
    ).toEqual([]);
  });
});

describe("fuzzy search for the palette", () => {
  it("requires every word and prefers word starts", () => {
    expect(score("тем", "Сменить тему оформления")).not.toBeNull();
    expect(score("тем пример", "Сменить тему оформления")).toBeNull();
    expect(score("тем", "Сменить тему")!).toBeGreaterThan(score("ему", "Сменить тему")!);
  });
  it("treats ё and е as the same letter and ranks", () => {
    expect(score("тёмная", "темная тема")).not.toBeNull();
    const items = ["Открыть Положение", "Сменить тему", "Тема документа"];
    expect(rank(items, "тем", (s) => s)[0]).toBe("Тема документа");
    expect(rank(items, "", (s) => s)).toHaveLength(3);
  });
});

describe("stages", () => {
  it("maps server stages to steps", () => {
    expect(stageIndex("load")).toBe(0);
    expect(stageIndex("extract")).toBe(3);
    expect(stageIndex("done")).toBe(STAGES.length);
    expect(stageIndex("unknown")).toBe(0);
  });
});

describe("suggestions", () => {
  const types = [{ id: "study", title: "Справка", short_title: "С", source: "", fields: [], examples: [], suggestions: [{ kind: "check", text: "Хватит ли справки?" }] }] as DocTypeSummary[];
  it("uses the generic set for 'other document'", () => {
    expect(suggestionsFor(types, "generic", null, []).map((s) => s.text)).toContain("О чём документ?");
  });
  it("follows the detected type when auto and falls back to defaults", () => {
    expect(suggestionsFor(types, "auto", "study", [])[0]!.text).toBe("Хватит ли справки?");
    expect(suggestionsFor([], "auto", null, [{ kind: "rules", text: "Сколько дней?" }]).map((s) => s.kind)).toContain("rules");
  });
});

describe("cropAround", () => {
  it("returns null without boxes", () => {
    expect(cropAround([], 1000, 1400)).toBeNull();
  });
  it("pads the boxes, stays inside the image and is never too narrow", () => {
    const g = cropAround([{ page: 0, x0: 100, y0: 200, x1: 180, y1: 230 }], 1000, 1400)!;
    expect(g.x).toBeGreaterThanOrEqual(0);
    expect(g.y).toBeGreaterThanOrEqual(0);
    expect(g.x + g.w).toBeLessThanOrEqual(1000);
    expect(g.y + g.h).toBeLessThanOrEqual(1400);
    expect(g.w).toBeGreaterThanOrEqual(380);
    expect(g.x).toBeLessThanOrEqual(100);
    expect(g.x + g.w).toBeGreaterThanOrEqual(180);
  });
  it("keeps a wide landscape shape even around a large field such as a stamp", () => {
    const g = cropAround([{ page: 0, x0: 400, y0: 900, x1: 700, y1: 1150 }], 1000, 1400)!;
    expect(g.w / g.h).toBeGreaterThanOrEqual(1.2);
    expect(g.y).toBeLessThanOrEqual(900);
    expect(g.y + g.h).toBeGreaterThanOrEqual(1150);
    expect(g.x).toBeLessThanOrEqual(400);
    expect(g.x + g.w).toBeGreaterThanOrEqual(700);
    // на узком снимке фрагмент подрезается по высоте, но поле остаётся целиком
    const narrow = cropAround([{ page: 0, x0: 100, y0: 500, x1: 300, y1: 800 }], 400, 1400)!;
    expect(narrow.w / narrow.h).toBeGreaterThanOrEqual(1.2);
    expect(narrow.y).toBeLessThanOrEqual(500);
    expect(narrow.y + narrow.h).toBeGreaterThanOrEqual(800);
  });
  it("clamps the crop to the image corner", () => {
    const g = cropAround([{ page: 0, x0: 950, y0: 1380, x1: 1000, y1: 1400 }], 1000, 1400)!;
    expect(g.x + g.w).toBeLessThanOrEqual(1000);
    expect(g.y + g.h).toBeLessThanOrEqual(1400);
    expect(g.px).toBeLessThanOrEqual(100);
  });
});
