import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { api } from "../api";
import { parseHash } from "../App";
import { SubmitPanel } from "../components/desk/SubmitPanel";
import { LoginDialog } from "../components/LoginDialog";
import { NavBar } from "../components/NavBar";
import { SNOOZE_DAYS, TelegramBanner, bannerSnoozed, snoozeBanner } from "../components/TelegramBanner";
import { AccountProvider, useAccount } from "../hooks/useAccount";
import { resetActivity, setActivity } from "../lib/activity";
import { initDataFromHash } from "../lib/telegram";
import type { AccountMe, AccountTicket, AccountUser } from "../types";
import { ToastProvider } from "../ui/Toasts";
import { AccountView } from "../views/AccountView";

vi.mock("../lib/telegram", async (orig) => ({ ...(await orig<typeof import("../lib/telegram")>()), bootMiniApp: vi.fn(async () => ({ initData: "signed-data", scheme: "dark" as const })) }));

const TG = { enabled: true, login: true, bot: "dekanat_test_bot", mini_app: true };
const USER: AccountUser = { id: 1, name: "Мария Орлова", username: "maria_o", notify: true, created_at: 1760000000, linked_web: true };
const anon: AccountMe = { user: null, telegram: TG };
const signed: AccountMe = { user: USER, telegram: TG };
const ticket = (over: Partial<AccountTicket> = {}): AccountTicket => ({ id: 12, status: "in_progress", topic: "Перевод из другого вуза", created_at: "2026-10-14T07:00:00+00:00", updated_at: "2026-10-14T09:00:00+00:00", reply: null, reply_at: null, ...over });

function mount(ui: ReactNode, me: AccountMe = anon) {
  vi.spyOn(api.account, "me").mockResolvedValue(me);
  return render(
    <ToastProvider>
      <AccountProvider>{ui}</AccountProvider>
    </ToastProvider>,
  );
}

beforeAll(() => {
  // jsdom не открывает <dialog> сам: повторяем то, что делает браузер, чтобы содержимое окна было доступно запросам
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
    this.dispatchEvent(new Event("close"));
  };
});

beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  history.replaceState(null, "", "/");
});
afterEach(() => {
  resetActivity();
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("Telegram Mini App bridge", () => {
  it("takes the signed data out of the address hash", () => {
    expect(initDataFromHash("#tgWebAppData=query_id%3DAAH%26user%3D%257B%2522id%2522%253A1%257D%26hash%3Dabc&tgWebAppVersion=8.0")).toBe("query_id=AAH&user=%7B%22id%22%3A1%7D&hash=abc");
    expect(initDataFromHash("#/check")).toBe("");
    expect(initDataFromHash("")).toBe("");
  });

  it("opens the account page by address", () => {
    expect(parseHash("#/account")).toEqual({ route: "account", clause: null });
  });
});

describe("banner snooze", () => {
  it("postpones for longer each time and then is quiet", () => {
    const t0 = 1_800_000_000_000;
    expect(bannerSnoozed(t0)).toBe(false);
    expect(snoozeBanner(t0)).toBe(SNOOZE_DAYS[0]);
    expect(bannerSnoozed(t0 + 2 * 86_400_000)).toBe(true);
    expect(bannerSnoozed(t0 + 4 * 86_400_000)).toBe(false);
    expect(snoozeBanner(t0 + 4 * 86_400_000)).toBe(SNOOZE_DAYS[1]);
    expect(snoozeBanner(t0 + 12 * 86_400_000)).toBe(SNOOZE_DAYS[2]);
    expect(snoozeBanner(t0 + 50 * 86_400_000)).toBe(SNOOZE_DAYS[2]); // дальше — раз в месяц
  });

  it("survives broken storage", () => {
    localStorage.setItem("dekanat.tgBanner", "{not json");
    expect(bannerSnoozed()).toBe(false);
    expect(() => snoozeBanner()).not.toThrow();
  });
});

describe("TelegramBanner", () => {
  it("offers the login with the benefits and says it is optional", async () => {
    mount(<TelegramBanner />);
    const banner = await screen.findByRole("complementary", { name: /Вход через Telegram/ });
    expect(within(banner).getByText(/по желанию/)).toBeInTheDocument();
    expect(within(banner).getByText(/на телефоне — закончите на компьютере/)).toBeInTheDocument();
    expect(within(banner).getByText(/придёт прямо в Telegram/)).toBeInTheDocument();
    expect(within(banner).getByText(/без кода/)).toBeInTheDocument();
    expect(within(banner).getByText("Без входа всё работает как раньше.")).toBeInTheDocument();
    expect(within(banner).getByRole("button", { name: /Войти через Telegram/ })).toBeInTheDocument();
    expect(within(banner).getByRole("button", { name: "Напомнить позже" })).toBeInTheDocument();
  });

  it("is a detailed form: three benefits with explanations, the three steps and what is stored", async () => {
    mount(<TelegramBanner />);
    const banner = await screen.findByRole("complementary", { name: /Вход через Telegram/ });
    const perks = within(banner).getAllByRole("listitem").filter((li) => li.closest("ul"));
    expect(perks).toHaveLength(3);
    expect(within(banner).getByText("Анкета везде")).toBeInTheDocument();
    expect(within(banner).getByText("Ответ в Telegram")).toBeInTheDocument();
    expect(within(banner).getByText("Обращения без кода")).toBeInTheDocument();
    const steps = within(within(banner).getByRole("list", { name: "Как войти" })).getAllByRole("listitem");
    expect(steps.map((x) => x.textContent)).toEqual(["Нажмите «Войти»", "Откройте бота и подтвердите вход", "Вернитесь на сайт — вход выполнится сам"]);
    expect(within(banner).getByText(/Из Telegram мы получаем только имя и @имя/)).toBeInTheDocument();
    expect(within(banner).getByText(/телефон и почту не запрашиваем/)).toBeInTheDocument();
  });

  it("'remind me later' hides it now and keeps it hidden after a reload", async () => {
    const { unmount } = mount(<TelegramBanner />);
    await userEvent.click(await screen.findByRole("button", { name: "Напомнить позже" }));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    expect(await screen.findByText(/напомню через 3 дня/)).toBeInTheDocument();
    unmount();
    mount(<TelegramBanner />);
    await waitFor(() => expect(api.account.me).toHaveBeenCalled());
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("comes back once the reminder period is over", async () => {
    localStorage.setItem("dekanat.tgBanner", JSON.stringify({ until: Date.now() - 1000, n: 1 }));
    mount(<TelegramBanner />);
    expect(await screen.findByRole("complementary")).toBeInTheDocument();
  });

  it("steps aside as soon as the person starts working on the desk", async () => {
    mount(<TelegramBanner />);
    expect(await screen.findByRole("complementary")).toBeInTheDocument();
    act(() => setActivity({ busy: true, fraction: 0.2, label: "Чтение текста" }));
    await waitFor(() => expect(screen.queryByRole("complementary")).not.toBeInTheDocument());
    act(() => resetActivity());
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument(); // и не возвращается, пока человек на странице
  });

  it("stays away for signed-in people and where the bot is off", async () => {
    const { unmount } = mount(<TelegramBanner />, signed);
    await waitFor(() => expect(api.account.me).toHaveBeenCalled());
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
    unmount();
    mount(<TelegramBanner />, { user: null, telegram: { enabled: false, login: false, bot: null, mini_app: false } });
    await waitFor(() => expect(api.account.me).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("opens the login window", async () => {
    function Probe() {
      const a = useAccount();
      return <output>{a.loginOpen ? "open" : "closed"}</output>;
    }
    mount(
      <>
        <TelegramBanner />
        <Probe />
      </>,
    );
    await userEvent.click(await screen.findByRole("button", { name: /Войти через Telegram/ }));
    expect(screen.getByText("open")).toBeInTheDocument();
  });
});

describe("Mini App sign-in", () => {
  it("exchanges the signed data for a session before the page content appears and hides the banner", async () => {
    history.replaceState(null, "", "/?tg=1");
    const webapp = vi.spyOn(api.account, "webapp").mockResolvedValue({ user: USER });
    mount(
      <>
        <TelegramBanner />
        <p>содержимое</p>
      </>,
      signed,
    );
    expect(await screen.findByText("содержимое")).toBeInTheDocument();
    expect(webapp).toHaveBeenCalledWith("signed-data");
    expect(document.documentElement.dataset.tg).toBe("1");
    expect(screen.queryByRole("complementary")).not.toBeInTheDocument();
  });

  it("carries on as a normal visitor if the sign-in fails", async () => {
    history.replaceState(null, "", "/?tg=1");
    vi.spyOn(api.account, "webapp").mockRejectedValue(new Error("Данные Telegram устарели"));
    mount(<p>содержимое</p>);
    expect(await screen.findByText("содержимое")).toBeInTheDocument();
  });
});

describe("LoginDialog", () => {
  function Open() {
    const a = useAccount();
    return (
      <>
        <button type="button" onClick={a.openLogin}>
          открыть
        </button>
        <output>{a.user ? `вошёл: ${a.user.name}` : "аноним"}</output>
        <LoginDialog />
      </>
    );
  }
  const start = { token: "tok123", link: "https://t.me/dekanat_test_bot?start=login_tok123", expires_in: 300, bot: "dekanat_test_bot" };

  it("shows the bot link and signs in by itself once confirmed in Telegram", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api.account, "loginStart").mockResolvedValue(start);
    const poll = vi.spyOn(api.account, "loginPoll").mockResolvedValueOnce({ status: "pending" }).mockResolvedValue({ status: "confirmed", user: USER, moved: { dialog: true, document: false, files: 0 } });
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    const link = await screen.findByRole("link", { name: /Открыть @dekanat_test_bot/ });
    expect(link).toHaveAttribute("href", start.link);
    expect(link).toHaveAttribute("target", "_blank");
    expect(screen.getByText(/Подтверждение нужно, чтобы никто не вошёл/)).toBeInTheDocument();
    expect(screen.getByText(/ссылка действует ещё/)).toHaveTextContent("5:00");
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(poll).toHaveBeenCalledWith("tok123", expect.anything());
    expect(screen.getByText("аноним")).toBeInTheDocument(); // подтверждения ещё нет
    await act(() => vi.advanceTimersByTimeAsync(2100));
    await waitFor(() => expect(screen.getByText("вошёл: Мария Орлова")).toBeInTheDocument());
    expect(await screen.findByText(/Начатое на сайте перенесено в аккаунт/)).toBeInTheDocument();
  });

  it("checks at once when the person comes back from Telegram to the tab", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api.account, "loginStart").mockResolvedValue(start);
    const poll = vi.spyOn(api.account, "loginPoll").mockResolvedValue({ status: "pending" });
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    await screen.findByRole("link", { name: /Открыть @/ });
    await act(async () => {}); // эффекты опроса подключены
    const before = poll.mock.calls.length;
    act(() => {
      window.dispatchEvent(new Event("focus")); // вернулись на вкладку — не ждём очередного опроса
    });
    await waitFor(() => expect(poll.mock.calls.length).toBeGreaterThan(before));
  });

  it("explains a refusal and offers to try again", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const startSpy = vi.spyOn(api.account, "loginStart").mockResolvedValue(start);
    vi.spyOn(api.account, "loginPoll").mockResolvedValue({ status: "denied" });
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    await screen.findByRole("link", { name: /Открыть @/ });
    await act(() => vi.advanceTimersByTimeAsync(2100));
    expect(await screen.findByText(/Вход отклонён в Telegram/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Попробовать снова" }));
    await waitFor(() => expect(startSpy).toHaveBeenCalledTimes(2));
  });

  it("asks for a fresh link when the old one expires", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api.account, "loginStart").mockResolvedValue({ ...start, expires_in: 3 });
    vi.spyOn(api.account, "loginPoll").mockResolvedValue({ status: "pending" });
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    await screen.findByRole("link", { name: /Открыть @/ });
    await act(() => vi.advanceTimersByTimeAsync(3500));
    expect(await screen.findByText(/Ссылка устарела/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Получить новую ссылку" })).toBeInTheDocument();
  });

  it("shows the server's message when the login cannot start", async () => {
    vi.spyOn(api.account, "loginStart").mockRejectedValue(new Error("Бот ещё запускается. Попробуйте через минуту."));
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Бот ещё запускается");
  });

  it("stops polling when the window is closed", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    vi.spyOn(api.account, "loginStart").mockResolvedValue(start);
    const poll = vi.spyOn(api.account, "loginPoll").mockResolvedValue({ status: "pending" });
    mount(<Open />);
    fireEvent.click(await screen.findByRole("button", { name: "открыть" }));
    await screen.findByRole("link", { name: /Открыть @/ });
    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    const before = poll.mock.calls.length;
    await act(() => vi.advanceTimersByTimeAsync(7000));
    expect(poll.mock.calls.length).toBe(before);
  });
});

describe("NavBar with an account", () => {
  const props = { route: "check" as const, onRoute: () => {}, theme: "system" as const, onThemeSet: () => {}, onPalette: () => {}, onHelp: () => {} };
  const openMenu = () => userEvent.click(screen.getByRole("button", { name: /^Меню/ }));

  it("has nothing about login when the bot is off", () => {
    render(<NavBar {...props} />);
    expect(document.querySelector(".acc-chip")).toBeNull();
  });

  it("offers the login in the menu for a visitor", async () => {
    const onLogin = vi.fn();
    render(<NavBar {...props} account={{ user: null, canLogin: true, onLogin, onLogout: () => {} }} />);
    expect(document.querySelector(".acc-chip")).toBeNull(); // панель остаётся спокойной
    await openMenu();
    await userEvent.click(screen.getByRole("button", { name: /Войти через Telegram/ }));
    expect(onLogin).toHaveBeenCalledTimes(1);
  });

  it("shows the person's initial, the account page and the logout", async () => {
    const onLogout = vi.fn();
    render(<NavBar {...props} account={{ user: USER, canLogin: true, onLogin: () => {}, onLogout }} />);
    const chip = screen.getByRole("link", { name: /Аккаунт: Мария Орлова/ });
    expect(chip).toHaveTextContent("М");
    expect(chip).toHaveAttribute("href", "#/account");
    await openMenu();
    expect(screen.getByRole("link", { name: /^Мария Орлова/, hidden: true })).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(onLogout).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: /Войти через Telegram/ })).not.toBeInTheDocument();
  });

  it("keeps the two sections intact", () => {
    render(<NavBar {...props} account={{ user: USER, canLogin: true, onLogin: () => {}, onLogout: () => {} }} />);
    expect(within(screen.getByRole("navigation", { name: "Разделы" })).getAllByRole("link")).toHaveLength(2);
  });
});

describe("AccountView", () => {
  const tickets = [ticket(), ticket({ id: 9, status: "done", topic: "Зачисление", reply: "Принесите оригинал справки", reply_at: "2026-10-12T10:00:00+00:00" })];

  it("invites a visitor to sign in", async () => {
    mount(<AccountView />);
    expect(await screen.findByRole("heading", { name: "Вход через Telegram" })).toBeInTheDocument();
    expect(screen.getByText(/без входа всё работает как раньше/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Войти через Telegram/ })).toBeInTheDocument();
  });

  it("says plainly when the bot is not enabled", async () => {
    mount(<AccountView />, { user: null, telegram: { enabled: false, login: false, bot: null, mini_app: false } });
    expect(await screen.findByText(/не включён/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Войти через Telegram/ })).not.toBeInTheDocument();
  });

  it("lists tickets without a code and opens the reply", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets });
    mount(<AccountView />, signed);
    expect(await screen.findByText("Мария Орлова", { selector: "h1" })).toBeInTheDocument();
    expect(screen.getByText(/@maria_o/)).toBeInTheDocument();
    const rows = await screen.findAllByRole("button", { name: /№ \d+/ });
    expect(rows).toHaveLength(2);
    expect(screen.queryByText("Принесите оригинал справки")).not.toBeInTheDocument();
    await userEvent.click(rows[1]!);
    expect(screen.getByText("Принесите оригинал справки")).toBeInTheDocument();
    expect(rows[1]).toHaveAttribute("aria-expanded", "true");
  });

  it("says there are no tickets yet", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    mount(<AccountView />, signed);
    expect(await screen.findByText(/Пока нет обращений/)).toBeInTheDocument();
  });

  it("switches the Telegram notifications", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    const set = vi.spyOn(api.account, "setNotify").mockResolvedValue({ user: { ...USER, notify: false } });
    mount(<AccountView />, signed);
    const toggle = await screen.findByRole("switch", { name: /Присылать уведомления/ });
    expect(toggle).toBeChecked();
    await userEvent.click(toggle);
    expect(set).toHaveBeenCalledWith(false);
    await waitFor(() => expect(toggle).not.toBeChecked());
  });

  it("links an earlier ticket by number and code", async () => {
    const list = vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    const link = vi.spyOn(api.account, "linkTicket").mockResolvedValue({ ticket: ticket() });
    mount(<AccountView />, signed);
    await userEvent.click(await screen.findByText(/Отправляли обращение раньше/));
    const button = screen.getByRole("button", { name: "Привязать" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getAllByRole("textbox")[0]!, "12");
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "abcd-2345");
    await userEvent.click(button);
    expect(link).toHaveBeenCalledWith(12, "ABCD-2345");
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("shows why linking failed", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    vi.spyOn(api.account, "linkTicket").mockRejectedValue(new Error("Это обращение уже привязано к другому аккаунту."));
    mount(<AccountView />, signed);
    await userEvent.click(await screen.findByText(/Отправляли обращение раньше/));
    await userEvent.type(screen.getAllByRole("textbox")[0]!, "3");
    await userEvent.type(screen.getByPlaceholderText("ABCD-EFGH"), "AAAABBBB");
    await userEvent.click(screen.getByRole("button", { name: "Привязать" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("уже привязано");
  });

  it("deletes the account only after confirmation", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    const remove = vi.spyOn(api.account, "remove").mockResolvedValue({ ok: true });
    vi.spyOn(api.account, "logout").mockResolvedValue({ ok: true });
    mount(<AccountView />, signed);
    await userEvent.click(await screen.findByRole("button", { name: /Удалить аккаунт/ }));
    expect(remove).not.toHaveBeenCalled();
    expect(await screen.findByText(/Уже отправленные обращения останутся у деканата/)).toBeInTheDocument();
    await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Удалить аккаунт" }));
    await waitFor(() => expect(remove).toHaveBeenCalledTimes(1));
  });

  it("offers a download of everything stored", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    mount(<AccountView />, signed);
    const dl = await screen.findByRole("link", { name: /Скачать мои данные/ });
    expect(dl).toHaveAttribute("href", "/api/account/export");
    expect(dl).toHaveAttribute("download");
  });

  it("logs out from the page", async () => {
    vi.spyOn(api.account, "tickets").mockResolvedValue({ tickets: [] });
    const out = vi.spyOn(api.account, "logout").mockResolvedValue({ ok: true });
    mount(<AccountView />, signed);
    await userEvent.click(await screen.findByRole("button", { name: /Выйти/ }));
    expect(out).toHaveBeenCalledTimes(1);
    expect(await screen.findByRole("heading", { name: "Вход через Telegram" })).toBeInTheDocument();
  });
});

describe("after sending a ticket", () => {
  const preview = { delivery: { channel: "queue", available: true, reason: "" }, payload: { request: "перевод", fields: { ФИО: "Иванов Иван" } }, excluded: [], attachment: true, state: "completed" as const, already_sent: null, blocked: [], ticket_code: "", mail: { available: false, reason: "почта не настроена" } };
  const delivery = { channel: "queue", available: true, reason: "" };

  beforeEach(() => {
    vi.spyOn(api.assistant, "submission").mockResolvedValue(preview);
    vi.spyOn(api.assistant, "submit").mockResolvedValue({ sent: true, ticket_id: 7, code: "K7M2-QX9P", message: "Обращение передано." });
  });

  async function sendTicket() {
    await userEvent.click(await screen.findByRole("button", { name: /Передать в деканат/ }));
    await userEvent.click(await screen.findByRole("checkbox", { name: /Подтверждаю/ }));
    await userEvent.click(screen.getByRole("button", { name: /Отправить/ }));
    await screen.findByText(/№ 7 · K7M2-QX9P/);
  }

  it("offers a visitor to get the answer in Telegram and promises the ticket will follow the account", async () => {
    function Probe() {
      return <output>{useAccount().loginOpen ? "login-open" : "login-closed"}</output>;
    }
    mount(
      <>
        <SubmitPanel delivery={delivery} />
        <Probe />
      </>,
    );
    await sendTicket();
    expect(screen.getByText(/Обращение привяжется к аккаунту само/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Получать ответ в Telegram/ }));
    expect(screen.getByText("login-open")).toBeInTheDocument();
  });

  it("tells a signed-in person that the answer will come to Telegram", async () => {
    mount(<SubmitPanel delivery={delivery} />, signed);
    await sendTicket();
    expect(screen.getByText(/Ответ деканата придёт вам в Telegram/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Получать ответ в Telegram/ })).not.toBeInTheDocument();
  });

  it("says so when the notifications are off", async () => {
    mount(<SubmitPanel delivery={delivery} />, { user: { ...USER, notify: false }, telegram: TG });
    await sendTicket();
    expect(screen.getByText(/Уведомления в Telegram выключены/)).toBeInTheDocument();
  });

  it("stays silent where Telegram is not enabled", async () => {
    mount(<SubmitPanel delivery={delivery} />, { user: null, telegram: { enabled: false, login: false, bot: null, mini_app: false } });
    await sendTicket();
    expect(screen.queryByText(/Telegram/)).not.toBeInTheDocument();
  });
});
