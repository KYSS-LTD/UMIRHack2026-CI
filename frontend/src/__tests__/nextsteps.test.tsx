import { render, screen } from "@testing-library/react";
import { api } from "../api";
import { NextSteps } from "../components/desk/NextSteps";

describe("NextSteps", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows what to bring and the estimated decision date, with the clause", async () => {
    vi.spyOn(api.assistant, "nextSteps").mockResolvedValue({
      documents: { text: "Для перевода нужна справка об обучении. (п. 7.2 Положения)", sources: ["п. 7.2"] },
      deadline: { days: 10, working: true, date: "2026-10-28", source: "п. 7.3", text: "..." },
    });
    render(<NextSteps />);
    expect(await screen.findByText(/справка об обучении/)).toBeInTheDocument();
    expect(screen.getByText(/Ориентировочно до 28 октября 2026: 10 рабочих дней по п\. 7\.3 Положения, без учёта праздников/)).toBeInTheDocument();
    expect(screen.getByText(/Окончательное решение принимает сотрудник деканата/)).toBeInTheDocument();
  });

  it("shows nothing when the regulation says nothing about it", async () => {
    const spy = vi.spyOn(api.assistant, "nextSteps").mockResolvedValue({ documents: null, deadline: null });
    const { container } = render(<NextSteps />);
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.querySelector(".next-steps")).toBeNull();
  });

  it("stays silent when the hint cannot be loaded", async () => {
    const spy = vi.spyOn(api.assistant, "nextSteps").mockRejectedValue(new Error("нет сети"));
    const { container } = render(<NextSteps />);
    await vi.waitFor(() => expect(spy).toHaveBeenCalled());
    expect(container.querySelector(".next-steps")).toBeNull();
  });
});
