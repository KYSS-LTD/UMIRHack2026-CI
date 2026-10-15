import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { NavBar } from "../components/NavBar";
import { placeBubble, Tour, tourSteps } from "../components/Tour";

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
  it("shows the steps of an empty desk, or of a desk with a document when one is open", () => {
    const empty = document.createElement("div");
    expect(tourSteps("check", empty).map((s) => s.target)).toContain(".tray .tray-actions");
    const doc = document.createElement("div");
    doc.innerHTML = '<section class="viewer"></section>';
    const targets = tourSteps("check", doc).map((s) => s.target);
    expect(targets).toContain(".verdict");
    expect(targets).not.toContain(".tray .tray-actions");
    expect(tourSteps("rules", empty).map((s) => s.target)).toContain(".reg .search");
    expect(tourSteps("account", empty).map((s) => s.target)).toEqual([".nav .switch"]); // на остальных страницах — один шаг про разделы
  });

  it("keeps only the main things: at most four steps on any screen", () => {
    const doc = document.createElement("div");
    doc.innerHTML = '<section class="viewer"></section>';
    for (const route of ["check", "rules", "status", "account"] as const) {
      expect(tourSteps(route, document.createElement("div")).length).toBeLessThanOrEqual(4);
      expect(tourSteps(route, doc).length).toBeLessThanOrEqual(4);
    }
  });
});

describe("Tour", () => {
  const Page = ({ open = true, onClose = noop, hasSend = true }: { open?: boolean; onClose?: () => void; hasSend?: boolean }) => (
    <>
      <div className="nav">
        <nav className="switch" />
      </div>
      <div className="tray">
        <div className="tray-actions" />
      </div>
      <div className="dock">
        <div className="dock-field" />
        {hasSend && <button type="button" className="send" />}
      </div>
      <Tour open={open} route="check" onClose={onClose} isVisible={() => true} />
    </>
  );

  it("starts with the first visible step, with a counter, text and an arrow", () => {
    const { container } = render(<Page />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Положите документ" })).toBeInTheDocument();
    expect(screen.getByText(/Подсказка 1 из 4/)).toBeInTheDocument(); // файл, вопрос, «Проверить», «Положение»
    expect(document.querySelector(".tour-arrow path")).not.toBeNull();
    expect(document.querySelector(".tour-spot")).not.toBeNull();
    expect(container).toBeTruthy();
  });

  it("skips steps whose element is not on the screen", () => {
    render(<Page hasSend={false} />);
    expect(screen.getByText(/Подсказка 1 из 3/)).toBeInTheDocument();
  });

  it("walks forward and back, and finishes with «Готово»", async () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} />);
    expect(screen.getByRole("button", { name: /Назад/ })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: /Далее/ }));
    expect(screen.getByRole("heading", { name: "Задайте вопрос" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Назад/ }));
    expect(screen.getByRole("heading", { name: "Положите документ" })).toBeInTheDocument();
    for (let i = 0; i < 4; i++) await userEvent.click(screen.getByRole("button", { name: /Далее|Готово/ }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("is controlled from the keyboard: arrows move, Esc closes", () => {
    const onClose = vi.fn();
    render(<Page onClose={onClose} />);
    fireEvent.keyDown(window, { key: "ArrowRight" });
    expect(screen.getByRole("heading", { name: "Задайте вопрос" })).toBeInTheDocument();
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

  it("says so, instead of an empty frame, when there is nothing to point at", () => {
    render(<Tour open route="check" onClose={noop} isVisible={() => true} />);
    expect(screen.getByText(/нечего показывать/)).toBeInTheDocument();
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
