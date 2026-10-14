import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { api } from "../api";
import { parseHash } from "../App";
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
    expect(await screen.findByText("Рассматривается")).toBeInTheDocument();
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
  const preview = { delivery: { channel: "queue", available: true, reason: "" }, payload: { request: "перевод", fields: { ФИО: "Иванов Иван" } }, excluded: [], attachment: true, state: "completed" as const, already_sent: null, blocked: [], ticket_code: "" };

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
