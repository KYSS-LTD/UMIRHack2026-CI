import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { parseHash } from "../App";
import { statusOfferText } from "../lib/dialog";
import { SubmitPanel } from "../components/desk/SubmitPanel";
import { StatusView } from "../views/StatusView";

const status = { id: 12, status: "in_progress" as const, status_title: "Рассматривается", topic: "Перевод из другого вуза", created_at: "2026-10-14T07:00:00+00:00", updated_at: "2026-10-14T09:00:00+00:00", reply: "Приходите в кабинет 214", reply_at: "2026-10-14T09:00:00+00:00" };

describe("parseHash", () => {
  it("opens the status page with an optional ticket number", () => {
    expect(parseHash("#/status")).toEqual({ route: "status", clause: null });
    expect(parseHash("#/status/12")).toEqual({ route: "status", clause: "12" });
    expect(parseHash("#/rules/3.4")).toEqual({ route: "rules", clause: "3.4" });
    expect(parseHash("#/whatever")).toEqual({ route: "check", clause: null });
  });
});

describe("StatusView", () => {
  afterEach(() => vi.restoreAllMocks());

  it("asks for the number and the code, shows the status and the staff reply", async () => {
    const spy = vi.spyOn(api.assistant, "ticketStatus").mockResolvedValue(status);
    render(<StatusView initialId="12" />);
    const button = screen.getByRole("button", { name: /Узнать статус/ });
    expect(button).toBeDisabled(); // кода ещё нет
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "abcd-efgh");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(spy).toHaveBeenCalledWith(12, "ABCD-EFGH");
    expect(await screen.findByText("Рассматривается", { selector: ".status-stamp" })).toBeInTheDocument();
    expect(screen.getByText("Приходите в кабинет 214")).toBeInTheDocument();
  });

  it("shows the server's message when the number or the code is wrong", async () => {
    vi.spyOn(api.assistant, "ticketStatus").mockRejectedValue(new Error("Обращение с таким номером и кодом не найдено. Проверьте номер и код."));
    render(<StatusView initialId={null} />);
    await userEvent.type(screen.getByPlaceholderText("например, 12"), "5");
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "AAAABBBB");
    await userEvent.click(screen.getByRole("button", { name: /Узнать статус/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent("не найдено");
    expect(screen.queryByText("Обращение № 5")).not.toBeInTheDocument();
  });

  it("says so when there is no reply yet", async () => {
    vi.spyOn(api.assistant, "ticketStatus").mockResolvedValue({ ...status, status: "new", status_title: "Принято, ждёт рассмотрения", reply: null, reply_at: null });
    render(<StatusView initialId="12" />);
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "ABCDEFGH");
    await userEvent.click(screen.getByRole("button", { name: /Узнать статус/ }));
    expect(await screen.findByText(/Ответа пока нет/)).toBeInTheDocument();
  });
});

describe("SubmitPanel contact and status code", () => {
  afterEach(() => vi.restoreAllMocks());
  const preview = { delivery: { channel: "queue", available: true, reason: "" }, payload: { request: "перевод", fields: { ФИО: "Иванов Иван" } }, excluded: [], attachment: true, state: "completed" as const, already_sent: null, blocked: [], ticket_code: "", mail: { available: false, reason: "почта не настроена" } };

  it("sends the optional contact and then shows the number and the code once", async () => {
    vi.spyOn(api.assistant, "submission").mockResolvedValue(preview);
    const submit = vi.spyOn(api.assistant, "submit").mockResolvedValue({ sent: true, ticket_id: 7, code: "K7M2-QX9P", message: "Обращение передано." });
    render(<SubmitPanel delivery={{ channel: "queue", available: true, reason: "" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Передать в деканат/ }));
    await userEvent.type(await screen.findByPlaceholderText(/почта, телефон/), "ivan@example.com");
    expect(screen.getByText("ivan@example.com")).toBeInTheDocument(); // контакт виден в составе передаваемых данных
    await userEvent.click(screen.getByRole("checkbox", { name: /Подтверждаю/ }));
    await userEvent.click(screen.getByRole("button", { name: /Отправить/ }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith(true, false, "ivan@example.com"));
    expect(await screen.findByText(/№ 7 · K7M2-QX9P/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Проверить статус/ })).toHaveAttribute("href", "#/status/7");
  });
});

describe("the secretary offers the status page after sending", () => {
  afterEach(() => vi.restoreAllMocks());

  it("names the number and the code, tells honestly what happened to the letter and offers the page", () => {
    const base = { sent: true, ticket_id: 7, code: "K7M2-QX9P", message: "ok" };
    expect(statusOfferText({ ...base, mail: { status: "sent", to: "i***@example.com" } })).toBe(
      "Обращение № 7 отправлено в деканат. Код для проверки статуса: K7M2-QX9P — сохраните его. Письмо с номером и кодом отправлено на i***@example.com. Статус и ответ деканата будут на странице «Статус обращения». Открыть её?",
    );
    expect(statusOfferText({ ...base, mail: { status: "disabled", to: "i***@example.com" } })).toContain("Почта на сервере не настроена");
    expect(statusOfferText({ ...base, mail: { status: "failed", to: "i***@example.com" } })).toContain("отправить не удалось");
    expect(statusOfferText({ ...base, mail: { status: "skipped" } })).toContain("Письма уходят только на почту");
    expect(statusOfferText({ ...base, mail: { status: "none" } })).not.toMatch(/Письм|Почта/);
    expect(statusOfferText(base)).not.toMatch(/Письм|Почта/);
  });

  it("tells the student whether a letter will be sent before the sending", async () => {
    const preview = { delivery: { channel: "queue", available: true, reason: "" }, payload: { request: "перевод", fields: {} }, excluded: [], attachment: true, state: "completed" as const, already_sent: null, blocked: [], ticket_code: "" };
    const spy = vi.spyOn(api.assistant, "submission").mockResolvedValue({ ...preview, mail: { available: true, reason: "" } });
    const { unmount } = render(<SubmitPanel delivery={{ channel: "queue", available: true, reason: "" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Передать в деканат/ }));
    expect(await screen.findByText(/мы отправим на неё номер и код/)).toBeInTheDocument();
    unmount();
    spy.mockResolvedValue({ ...preview, mail: { available: false, reason: "почта не настроена" } });
    render(<SubmitPanel delivery={{ channel: "queue", available: true, reason: "" }} />);
    await userEvent.click(screen.getByRole("button", { name: /Передать в деканат/ }));
    expect(await screen.findByText(/Письма с кодом сейчас не отправляются/)).toBeInTheDocument();
  });

  it("tells the app about a fresh submission only once", async () => {
    const preview = { delivery: { channel: "queue", available: true, reason: "" }, payload: { request: "перевод", fields: {} }, excluded: [], attachment: true, state: "completed" as const, already_sent: null, blocked: [], ticket_code: "", mail: { available: false, reason: "" } };
    vi.spyOn(api.assistant, "submission").mockResolvedValue(preview);
    const sent = { sent: true, ticket_id: 7, code: "K7M2-QX9P", message: "ok" };
    vi.spyOn(api.assistant, "submit").mockResolvedValue(sent);
    const onSent = vi.fn();
    render(<SubmitPanel delivery={{ channel: "queue", available: true, reason: "" }} onSent={onSent} />);
    await userEvent.click(screen.getByRole("button", { name: /Передать в деканат/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /Подтверждаю/ }));
    await userEvent.click(screen.getByRole("button", { name: /Отправить/ }));
    await waitFor(() => expect(onSent).toHaveBeenCalledWith(sent));
    expect(onSent).toHaveBeenCalledTimes(1);
  });
});

describe("StatusView stages and refresh", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });
  const fill = async () => {
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "ABCDEFGH");
    await userEvent.click(screen.getByRole("button", { name: /Узнать статус/ }));
  };

  it("marks the current stage and the passed ones", async () => {
    vi.spyOn(api.assistant, "ticketStatus").mockResolvedValue(status); // in_progress
    render(<StatusView initialId="12" />);
    await fill();
    const steps = await screen.findByRole("list", { name: "Этапы обращения" });
    const items = Array.from(steps.querySelectorAll("li"));
    expect(items.map((li) => li.className)).toEqual(["done", "now", "todo"]);
    expect(items[1]).toHaveAttribute("aria-current", "step");
  });

  it("shows the decision stage as done for a processed request and as a refusal for a rejected one", async () => {
    const spy = vi.spyOn(api.assistant, "ticketStatus").mockResolvedValue({ ...status, status: "done", status_title: "Рассмотрено" });
    const { unmount } = render(<StatusView initialId="12" />);
    await fill();
    const done = await screen.findByRole("list", { name: "Этапы обращения" });
    expect(Array.from(done.querySelectorAll("li")).map((li) => li.className)).toEqual(["done", "done", "done"]);
    expect(screen.getByText("Рассмотрено", { selector: ".name" })).toBeInTheDocument();
    expect(screen.getByText(/статус больше не изменится/)).toBeInTheDocument();
    unmount();
    spy.mockResolvedValue({ ...status, status: "rejected", status_title: "Отклонено" });
    render(<StatusView initialId="12" />);
    await fill();
    const rejected = await screen.findByRole("list", { name: "Этапы обращения" });
    expect(Array.from(rejected.querySelectorAll("li")).map((li) => li.className)).toEqual(["done", "done", "done bad"]);
    expect(screen.getByText("Отклонено", { selector: ".name" })).toBeInTheDocument();
  });

  it("refreshes by hand and by itself every minute until the request is processed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true }); // до отрисовки: интервал автообновления создаётся на подменённых таймерах
    const spy = vi.spyOn(api.assistant, "ticketStatus").mockResolvedValue({ ...status, status: "new", status_title: "Принято, ждёт рассмотрения", reply: null, reply_at: null });
    render(<StatusView initialId="12" />);
    fireEvent.change(screen.getByPlaceholderText("ABCD-EFGH"), { target: { value: "abcdefgh" } });
    fireEvent.click(screen.getByRole("button", { name: /Узнать статус/ }));
    await screen.findByRole("list", { name: "Этапы обращения" });
    expect(spy).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: /Обновить/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    expect(spy).toHaveBeenLastCalledWith(12, "ABCDEFGH"); // код уходит как введён: регистр и дефис сервер не различает
    spy.mockResolvedValue({ ...status, status: "in_progress", status_title: "Рассматривается" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    expect(spy).toHaveBeenCalledTimes(3);
    expect(screen.getByText("Рассматривается", { selector: ".status-stamp" })).toBeInTheDocument(); // статус сменился без нажатия
    spy.mockResolvedValue({ ...status, status: "done", status_title: "Рассмотрено" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(61_000);
    });
    const calls = spy.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(180_000);
    });
    expect(spy.mock.calls.length).toBe(calls); // рассмотренное обращение больше не опрашивается
  });
});
