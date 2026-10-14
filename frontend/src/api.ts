import type {
  PagePreview,
  AnalysisResult,
  AssistantState,
  DocTypeSummary,
  FieldCorrection,
  GenerateResult,
  Health,
  JobSnapshot,
  MessageResult,
  RegulationInfo,
  SampleItem,
  SubmissionPreview,
  NextSteps,
  SubmitResult,
  TicketStatus,
  Suggestion,
} from "./types";

export class ApiError extends Error {
  status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Достаёт человекочитаемое сообщение из ответа FastAPI ({"detail": "..."} или список ошибок валидации). */
async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    const d = body?.detail;
    // стандартные английские тексты FastAPI — не для людей: «Not Found» бывает, когда сервер запущен со старой версией проекта
    if (d === "Not Found" || d === "Method Not Allowed") return "Сервер не знает этого действия: скорее всего, он запущен со старой версией проекта. Остановите сервер, запустите его заново и обновите страницу.";
    if (typeof d === "string") return d;
    if (Array.isArray(d)) return d.map((x) => x?.msg ?? String(x)).join("; ");
  } catch {
    /* тело не JSON */
  }
  if (res.status === 413) return "Файл слишком большой";
  if (res.status >= 500) return "Не получилось выполнить запрос. Попробуйте ещё раз чуть позже.";
  return `Ошибка ${res.status}`;
}

async function request<T>(url: string, init?: RequestInit, parse: "json" | "text" = "json"): Promise<T> {
  let res: Response;
  try {
    res = await fetch(url, init);
  } catch (e) {
    if ((e as Error)?.name === "AbortError") throw e;
    throw new ApiError("Нет связи с сервером. Проверьте подключение и повторите.", 0);
  }
  if (!res.ok) throw new ApiError(await errorMessage(res), res.status);
  return (parse === "text" ? res.text() : res.json()) as Promise<T>;
}

export const api = {
  health: () => request<Health>("/api/health"),
  doctypes: () => request<DocTypeSummary[]>("/api/doctypes"),
  ruleSuggestions: () => request<{ rules: Suggestion[] }>("/api/suggestions"),
  samples: () => request<SampleItem[]>("/api/samples"),
  sampleUrl: (name: string) => `/api/samples/${encodeURIComponent(name)}`,

  async startJob(input: { file?: Blob | null; fileName?: string; question: string; docType: string }, signal?: AbortSignal): Promise<{ id: string }> {
    const form = new FormData();
    if (input.file) form.append("file", input.file, input.fileName || (input.file as File).name || "document");
    form.append("question", input.question);
    form.append("doc_type", input.docType);
    return request<{ id: string }>("/api/jobs", { method: "POST", body: form, signal });
  },
  job: (id: string, signal?: AbortSignal) => request<JobSnapshot>(`/api/jobs/${id}`, { signal }),
  /** Первый лист файла (для PDF — настоящая страница вместо заглушки, пока идёт проверка). */
  async previewPage(file: Blob, fileName: string, signal?: AbortSignal): Promise<PagePreview> {
    const form = new FormData();
    form.append("file", file, fileName || (file as File).name || "document");
    return request<PagePreview>("/api/preview", { method: "POST", body: form, signal });
  },
  cancelJob: (id: string) => request<{ ok: boolean }>(`/api/jobs/${id}`, { method: "DELETE" }).catch(() => ({ ok: false })),

  regulation: () => request<RegulationInfo>("/api/regulation"),
  async uploadRegulation(file: File): Promise<{ name: string; clauses: number }> {
    const form = new FormData();
    form.append("file", file, file.name);
    return request("/api/regulation", { method: "PUT", body: form });
  },

  /** Анкета, документ сессии, DOCX и отправка в деканат. Сессия анонимная: HttpOnly-cookie, данные хранит сервер. */
  assistant: {
    state: () => request<AssistantState>("/api/assistant/state"),
    /** defer: сообщение не про анкету сервер не отвечает — стол сам запускает обычную проверку с прогрессом */
    message: (text: string, templateId: string | null, signal?: AbortSignal) =>
      request<MessageResult>("/api/assistant/message", json("POST", { text, template_id: templateId, defer: true }, signal)),
    generate: (templateId: string | null, signal?: AbortSignal) => request<GenerateResult>("/api/assistant/generate", json("POST", { template_id: templateId }, signal)),
    correctField: (unit: string, value: string, question: string) =>
      request<FieldCorrection>("/api/assistant/document/fields", json("PATCH", { unit, value, question })),
    submission: (includePassport: boolean) => request<SubmissionPreview>(`/api/assistant/submission?include_passport=${includePassport}`),
    submit: (confirm: boolean, includePassport: boolean, contact = "") =>
      request<SubmitResult>("/api/assistant/submission", json("POST", { confirm, include_passport: includePassport, contact })),
    nextSteps: () => request<NextSteps>("/api/assistant/next-steps"),
    ticketStatus: (id: number, code: string) => request<TicketStatus>("/api/assistant/status", json("POST", { id, code })),
    deleteData: () => request<{ ok: boolean; message: string }>("/api/assistant/data", { method: "DELETE" }),
  },
};

function json(method: string, body: unknown, signal?: AbortSignal): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal };
}

const sleep = (ms: number, signal?: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"));
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => {
      clearTimeout(t);
      reject(new DOMException("Aborted", "AbortError"));
    });
  });

export interface RunHandlers {
  onProgress: (snap: JobSnapshot) => void;
  onJobId?: (id: string) => void;
}

/**
 * Запускает проверку как фоновую задачу и опрашивает её статус до конца.
 * Возвращает результат или бросает ApiError (ошибка проверки) / AbortError (пользователь отменил).
 */
export async function runAnalysis(
  input: { file?: Blob | null; fileName?: string; question: string; docType: string },
  handlers: RunHandlers,
  signal: AbortSignal,
  pollMs = 450,
): Promise<AnalysisResult> {
  const { id } = await api.startJob(input, signal);
  handlers.onJobId?.(id);
  let failures = 0;
  for (;;) {
    await sleep(pollMs, signal);
    let snap: JobSnapshot;
    try {
      snap = await api.job(id, signal);
      failures = 0;
    } catch (e) {
      if ((e as Error).name === "AbortError") throw e;
      if (e instanceof ApiError && e.status === 404) throw e;
      if (++failures >= 5) throw e;
      continue;
    }
    handlers.onProgress(snap);
    if (snap.status === "done" && snap.result) return snap.result;
    if (snap.status === "error") throw new ApiError(snap.error || "Не удалось выполнить проверку", 500);
    if (snap.status === "cancelled") throw new DOMException("Cancelled", "AbortError");
  }
}
