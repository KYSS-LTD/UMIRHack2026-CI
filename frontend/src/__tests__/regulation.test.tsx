import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { loadRegulation } from "../hooks/useRegulation";
import { linkRefs } from "../lib/refs";
import type { RegulationInfo } from "../types";
import { ToastProvider } from "../ui/Toasts";
import { RegulationView } from "../views/RegulationView";

describe("linkRefs", () => {
  const known = new Set(["3.2", "3.4", "7.2"]);

  it("turns mentions of existing clauses into links, keeping the rest as text", () => {
    expect(linkRefs("Справка по п. 3.4 и пунктом 7.2 принимается; см. раздела 3.", known)).toEqual([
      "Справка по ",
      { ref: "3.4", text: "п. 3.4" },
      " и ",
      { ref: "7.2", text: "пунктом 7.2" },
      " принимается; см. раздела 3.",
    ]);
  });

  it("does not link numbers that are not clauses of the regulation or sections without a dot", () => {
    expect(linkRefs("по п. 9.9 и пункту 3", known)).toEqual(["по п. 9.9 и пункту 3"]);
    expect(linkRefs("Без ссылок", known)).toEqual(["Без ссылок"]);
    expect(linkRefs("", known)).toEqual([""]);
  });

  it("is case-insensitive and handles several forms and neighbours", () => {
    const parts = linkRefs("П. 3.2, пункта 3.4.", known);
    expect(parts.filter((p) => typeof p !== "string")).toEqual([{ ref: "3.2", text: "П. 3.2" }, { ref: "3.4", text: "пункта 3.4" }]);
    expect(parts.join("")).toContain("[object Object]"); // объекты — ссылки, строки — обычный текст между ними
    expect(parts[parts.length - 1]).toBe(".");
  });
});

describe("RegulationView details", () => {
  const info: RegulationInfo = {
    name: "polozhenie.md",
    editable: false,
    clauses: [
      { number: "3.2", label: "п. 3.2", text: "Справка должна содержать подпись и печать.", section: ["Справка об обучении"] },
      { number: "3.4", label: "п. 3.4", text: "Справка без реквизитов из п. 3.2 не принимается; см. п. 9.9.", section: ["Справка об обучении"] },
      { number: "d1:2.1", display: "2.1", label: "п. 2.1 (Правила общежития)", text: "Плата 1500 рублей.", section: [], document: { id: "d1", name: "Правила общежития" } },
    ],
  };
  const view = () => (
    <ToastProvider>
      <RegulationView focusClause={null} editable={false} />
    </ToastProvider>
  );

  beforeEach(() => {
    vi.spyOn(api, "regulation").mockResolvedValue(info);
    return loadRegulation(true);
  });
  afterEach(() => vi.restoreAllMocks());

  it("links a mention of an existing clause and leaves unknown ones as text", async () => {
    render(view());
    const link = await screen.findByRole("link", { name: "п. 3.2" });
    expect(link).toHaveAttribute("href", "#/rules/3.2");
    expect(screen.queryByRole("link", { name: "п. 9.9" })).not.toBeInTheDocument();
    expect(screen.getByText(/см\. п\. 9\.9/)).toBeInTheDocument();
  });

  it("shows documents of the knowledge base as their own section with the human number", async () => {
    render(view());
    expect(await screen.findByRole("heading", { name: "Документ «Правила общежития»" })).toBeInTheDocument();
    expect(screen.getByText("2.1")).toBeInTheDocument();
    expect(screen.queryByText("d1:2.1")).not.toBeInTheDocument();
    expect(screen.getByText(/и ещё 1 документ базы знаний/)).toBeInTheDocument();
  });

  it("copies the link and the text of a clause", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(view());
    await userEvent.click(await screen.findByRole("button", { name: "Скопировать ссылку на пункт 3.2" }));
    expect(writeText).toHaveBeenLastCalledWith(expect.stringMatching(/#\/rules\/3\.2$/));
    await userEvent.click(screen.getByRole("button", { name: "Скопировать текст пункта 3.4" }));
    expect(writeText).toHaveBeenLastCalledWith("п. 3.4 Положения: Справка без реквизитов из п. 3.2 не принимается; см. п. 9.9.");
    await userEvent.click(screen.getByRole("button", { name: "Скопировать текст пункта 2.1" }));
    expect(writeText).toHaveBeenLastCalledWith("п. 2.1 документа «Правила общежития»: Плата 1500 рублей.");
    expect(await screen.findByText("Пункт 2.1 скопирован")).toBeInTheDocument();
  });

  it("shows a skeleton while loading and a way back to the top", async () => {
    vi.spyOn(api, "regulation").mockReturnValue(new Promise(() => undefined));
    void loadRegulation(true); // запрос не завершается: пока он идёт, на месте пунктов заглушки
    const { container } = render(view());
    expect(container.querySelector(".reg-skeleton")).not.toBeNull();
    expect(screen.getByRole("button", { name: "Наверх" })).toHaveAttribute("data-show", "false");
  });

  it("filters with the search box and counts the results", async () => {
    render(view());
    await userEvent.type(await screen.findByRole("searchbox", { name: "Поиск по Положению" }), "плата");
    await waitFor(() => expect(screen.getByText("найдено: 1")).toBeInTheDocument());
    expect(screen.queryByText(/Справка должна содержать/)).not.toBeInTheDocument();
  });
});
