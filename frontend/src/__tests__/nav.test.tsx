import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NavBar } from "../components/NavBar";
import { getActivity, resetActivity, setActivity } from "../lib/activity";
import { useBus } from "../lib/bus";
import { plural } from "../lib/format";
import { Logo } from "../ui/Logo";

const noop = () => {};
const props = { route: "check" as const, onRoute: noop, theme: "system" as const, onThemeSet: noop, onPalette: noop, onHelp: noop };
const openMenu = () => userEvent.click(screen.getByRole("button", { name: /^Меню/ }));

afterEach(() => resetActivity());

describe("plural", () => {
  it("declines Russian nouns by number", () => {
    const f: [string, string, string] = ["пункт", "пункта", "пунктов"];
    expect([1, 2, 5, 11, 12, 21, 25, 101, 112].map((n) => plural(n, f))).toEqual(["пункт", "пункта", "пунктов", "пунктов", "пунктов", "пункт", "пунктов", "пункт", "пунктов"]);
  });
});

describe("activity store", () => {
  it("publishes changes once and resets", () => {
    setActivity({ busy: true, fraction: 0.4, label: "Чтение текста" });
    expect(getActivity()).toMatchObject({ busy: true, fraction: 0.4 });
    resetActivity();
    expect(getActivity().busy).toBe(false);
  });
});

describe("NavBar", () => {
  it("shows the two sections without counters and marks the current one", () => {
    render(<NavBar {...props} route="rules" />);
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    expect(within(nav).getAllByRole("link")).toHaveLength(2); // «Типы документов» из интерфейса убраны
    expect(within(nav).queryByRole("link", { name: /Типы документов/ })).not.toBeInTheDocument();
    expect(within(nav).getByRole("link", { name: /Положение/ })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: /Стол/ })).not.toHaveAttribute("aria-current");
    expect(within(nav).queryByText("25")).not.toBeInTheDocument();
  });

  it("keeps the panel light: search and one menu instead of a row of buttons", () => {
    render(<NavBar {...props} />);
    const tools = document.querySelector(".tools")!;
    const visible = within(tools as HTMLElement).getAllByRole("button").filter((b) => !b.closest(".menu-panel")); // содержимое закрытого меню скрыто (inert)
    expect(visible).toHaveLength(2);
    expect(document.querySelector(".menu-panel")).toHaveAttribute("inert");
    expect(screen.getByRole("button", { name: /^Меню/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("changes the theme from the menu", async () => {
    const onThemeSet = vi.fn();
    render(<NavBar {...props} onThemeSet={onThemeSet} />);
    await openMenu();
    const group = screen.getByRole("radiogroup", { name: "Тема оформления" });
    expect(within(group).getByRole("radio", { name: /Как в системе/ })).toHaveAttribute("aria-checked", "true");
    await userEvent.click(within(group).getByRole("radio", { name: /Тёмная/ }));
    expect(onThemeSet).toHaveBeenCalledWith("dark");
  });

  it("opens the command palette and the help", async () => {
    const onPalette = vi.fn();
    const onHelp = vi.fn();
    render(<NavBar {...props} onPalette={onPalette} onHelp={onHelp} />);
    await userEvent.click(screen.getByRole("button", { name: /Поиск и команды/ }));
    await openMenu();
    await userEvent.click(screen.getByRole("button", { name: /Горячие клавиши/ }));
    expect(onPalette).toHaveBeenCalledTimes(1);
    expect(onHelp).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /^Меню/ })).toHaveAttribute("aria-expanded", "false"); // после действия меню закрывается
  });

  it("closes the menu with Escape and leads staff to the admin page", async () => {
    render(<NavBar {...props} />);
    await openMenu();
    expect(screen.getByRole("link", { name: /Вход для сотрудников/ })).toHaveAttribute("href", "/admin");
    await userEvent.keyboard("{Escape}");
    expect(screen.getByRole("button", { name: /^Меню/ })).toHaveAttribute("aria-expanded", "false");
  });

  it("shows the live progress of a running check in the panel", () => {
    render(<NavBar {...props} />);
    expect(screen.queryByText("Чтение текста")).not.toBeInTheDocument();
    act(() => setActivity({ busy: true, fraction: 0.42, label: "Чтение текста" }));
    expect(screen.getByText("Чтение текста")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(document.querySelector(".nav")).toHaveAttribute("data-busy", "true");
  });

  it("shows no technical information: no versions, engines or system state", async () => {
    render(<NavBar {...props} />);
    await openMenu();
    const text = document.querySelector(".nav")!.textContent ?? "";
    for (const word of ["Tesseract", "OCR", "Версия", "API", "Языковая модель", "Состояние системы", "пунктов"]) expect(text).not.toContain(word);
  });
});

describe("Logo", () => {
  it("renders the seal, the sheet and the check", () => {
    const { container } = render(<Logo size={48} />);
    expect(container.querySelector(".logo-teeth")).not.toBeNull();
    expect(container.querySelector(".logo-sheet")).not.toBeNull();
    expect(container.querySelector(".logo-check")).not.toBeNull();
    expect(container.querySelector("svg")).toHaveAttribute("width", "48");
  });
  it("can be drawn without the tile background", () => {
    const { container } = render(<Logo bare />);
    expect(container.querySelectorAll("rect")).toHaveLength(0);
  });
});

describe("NavBar in the staff-page style", () => {
  it("has one flat switch; staff get 'Сотрудникам' in it, visitors do not", () => {
    const { rerender } = render(<NavBar {...props} />);
    const nav = screen.getByRole("navigation", { name: "Разделы" });
    expect(within(nav).getAllByRole("link").map((a) => a.textContent)).toEqual(["Стол", "Положение"]);
    rerender(<NavBar {...props} staff />);
    expect(within(screen.getByRole("navigation", { name: "Разделы" })).getByRole("link", { name: "Сотрудникам" })).toHaveAttribute("href", "/admin");
    expect(document.querySelectorAll(".staff-switch, .tab-ind, .search-pill")).toHaveLength(0);
  });

  it("search and menu are round icon buttons without the wide search pill", () => {
    render(<NavBar {...props} />);
    expect(screen.getByRole("button", { name: /Поиск и команды/ })).toHaveClass("round-btn");
    expect(screen.getByRole("button", { name: /^Меню/ })).toHaveClass("round-btn");
    expect(screen.queryByText("Ctrl K")).not.toBeInTheDocument();
  });
});

describe("the logo", () => {
  it("leads to the desk and clears the chat", async () => {
    const cleared = vi.fn();
    const onRoute = vi.fn();
    function Listen() {
      useBus("new-desk", cleared);
      return null;
    }
    render(
      <>
        <Listen />
        <NavBar {...props} onRoute={onRoute} />
      </>,
    );
    const logo = screen.getByRole("link", { name: /Онлайн-деканат — на стол/ });
    expect(logo).toHaveAttribute("href", "#/check");
    expect(logo).toHaveAttribute("title", expect.stringContaining("чат очищается")); // о последствиях сказано заранее
    await userEvent.click(logo);
    expect(onRoute).toHaveBeenCalledWith("check");
    await vi.waitFor(() => expect(cleared).toHaveBeenCalledTimes(1));
  });

  it("opens the desk first and clears afterwards when clicked on another page", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const cleared = vi.fn();
    function Listen() {
      useBus("new-desk", cleared);
      return null;
    }
    render(
      <>
        <Listen />
        <NavBar {...props} route="rules" />
      </>,
    );
    await userEvent.click(screen.getByRole("link", { name: /Онлайн-деканат — на стол/ }));
    expect(cleared).not.toHaveBeenCalled(); // стол ещё не открыт: очищать пока некому
    await vi.advanceTimersByTimeAsync(100);
    expect(cleared).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });
});
