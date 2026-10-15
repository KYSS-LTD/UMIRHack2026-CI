/** Типы ответов API (см. dekanat/models.py и dekanat/pipeline.py). Координаты рамок — в пикселях исходного снимка. */

export type Verdict = "ok" | "missing" | "no_answer";
export type FieldStatus = "found" | "partial" | "missing";

export interface Box {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
  /** снимок сделан под углом: четыре угла строки по часовой стрелке (тот же масштаб, что и x0…y1). Рамка идёт вдоль строки, а не по описанному прямоугольнику */
  quad?: [number, number][];
}

export interface PartResult {
  id: string;
  title: string;
  found: boolean;
  value: string;
  normalized: unknown;
  confidence: number;
  boxes: Box[];
  doubt?: string;
  /** значение исправлено пользователем на столе */
  corrected?: boolean;
}

export interface FieldResult {
  id: string;
  title: string;
  export_title: string;
  kind: string;
  status: FieldStatus;
  found: boolean;
  value: string;
  normalized: unknown;
  confidence: number;
  method: string;
  evidence: string;
  boxes: Box[];
  parts: PartResult[];
  /** причина сомнения (этап валидации): пусто, если значение подтверждено */
  doubt?: string;
  confirmed?: boolean;
  /** значение (или одна из частей) исправлено пользователем на столе */
  corrected?: boolean;
}

export interface Source {
  kind: "clause" | "field" | "document" | string;
  ref: string;
  label: string;
  quote: string;
  boxes: Box[];
}

export interface MissingItem {
  unit?: string;
  title: string;
  clause?: string;
  [key: string]: unknown;
}

export interface Answer {
  verdict: Verdict;
  text: string;
  intent: string;
  sources: Source[];
  missing: MissingItem[];
  related: Source[];
  confidence: number;
  notes: string[];
  debug: Record<string, unknown>;
  /** структурированный ответ: вывод, факты документа, пункты, недостающее и сомнительное, оговорка о решении деканата */
  report?: {
    disclaimer?: string;
    explanation?: string;
    explanation_note?: string;
  };
}

export interface TypeCandidate {
  id: string;
  title: string;
  share: number;
}

export interface DocTypeInfo {
  id: string | null;
  title: string | null;
  how: "auto" | "manual" | "generic" | "none";
  confidence: number;
  uncertain: boolean;
  ranking: { id: string; score: number }[];
  candidates: TypeCandidate[];
  reason: string;
}

export interface OcrPageInfo {
  index: number;
  width: number;
  height: number;
  variant: string | null;
  deskew_angle: number | null;
  rotation: number | null;
  score: number | null;
  mean_conf: number | null;
  words: number | null;
  seconds: number | null;
}

export interface PagePreview {
  page: number;
  width: number;
  height: number;
  data_url: string;
}

export interface AnalysisResult {
  doc_type: DocTypeInfo;
  ocr: { engine: string | null; pages: OcrPageInfo[]; text: string };
  fields: FieldResult[];
  answer: Answer;
  warnings: string[];
  timings: Record<string, number | boolean>;
  images: PagePreview[];
  export: Record<string, unknown>;
  /** повторная проверка того же файла: поля, исправленные пользователем раньше («поле» или «поле.часть») */
  corrected?: string[];
}

export type JobStatus = "queued" | "running" | "done" | "error" | "cancelled";

export interface JobSnapshot {
  id: string;
  status: JobStatus;
  stage: string;
  label: string;
  fraction: number;
  elapsed: number;
  result?: AnalysisResult;
  error?: string;
}

export interface Suggestion {
  kind: "check" | "field" | "rules" | "form";
  text: string;
}

export interface DocTypeSummary {
  id: string;
  title: string;
  short_title: string;
  source: string;
  fields: { id: string; title: string; kind: string; parts: { id: string; title: string }[] }[];
  examples: string[];
  suggestions: Suggestion[];
}

export interface Health {
  version: string;
  allow_edit: boolean;
  /** можно ли именно этому посетителю править Положение и типы (нужен вход администратора на /admin) */
  can_edit?: boolean;
  max_upload_mb: number;
  ocr: { available?: boolean; version?: string; languages?: string[]; [k: string]: unknown };
  doc_types: { id: string; title: string; fields: number }[];
  regulation: { name: string; clauses: number };
  llm: { backend: string; [k: string]: unknown };
  /** вошёл ли сотрудник деканата (для переключателя «Стол декана | Сотрудникам») */
  admin?: { authenticated?: boolean; configured?: boolean; protect_edit?: boolean };
  /** Telegram-бот и вход через него: выключено по умолчанию, сайт работает и без них */
  telegram?: TelegramInfo;
}

export interface TelegramInfo {
  enabled: boolean;
  /** вход через бота доступен: бот включён и известно его имя */
  login: boolean;
  bot: string | null;
  /** кнопка «Приложение» в боте (нужен https-адрес сайта) */
  mini_app: boolean;
}

/** Профиль вошедшего: только то, что нужно показать человеку (без Telegram-идентификатора). */
export interface AccountUser {
  id: number;
  name: string;
  username: string | null;
  notify: boolean;
  created_at: number | null;
  linked_web: boolean;
}

export interface AccountMe {
  user: AccountUser | null;
  telegram: TelegramInfo;
}

export interface LoginStart {
  already?: boolean;
  user?: AccountUser;
  token?: string;
  link?: string;
  expires_in?: number;
  bot?: string;
}

export type LoginPoll = { status: "pending" | "denied" | "expired" } | { status: "confirmed"; user: AccountUser; moved: { dialog: boolean; document: boolean; files: number } };

/** Обращение вошедшего: код не нужен, статус виден сразу. */
export interface AccountTicket {
  id: number;
  status: TicketStatus["status"];
  topic: string | null;
  created_at: string;
  updated_at: string;
  reply: string | null;
  reply_at: string | null;
}

export interface SampleItem {
  file: string;
  title: string;
  question: string;
  doc_type?: string;
}

export interface Clause {
  /** ключ пункта: «3.4» или «d2:3.4» у документа базы знаний */
  number: string;
  /** номер, как его видит человек: «3.4» (без служебного префикса документа) */
  display?: string;
  /** есть только у пунктов дополнительных документов базы знаний */
  document?: { id: string; name: string };
  label: string;
  text: string;
  section: string[];
}

export interface RegulationInfo {
  name: string;
  clauses: Clause[];
  editable: boolean;
}

/* ---------- анкета и документ сессии (/api/assistant, см. dekanat/web/assistant.py) ---------- */

export type DialogState = "idle" | "screening" | "review" | "document_generation" | "completed";

export interface DialogInfo {
  state: DialogState;
  scenario: string;
  current: string | null;
  editing: boolean;
  case_id: number;
  answered: string[];
  version: number;
}

/** Реплика секретаря в анкете. kind: question | clarify | error | offer | info | review | generation | done | cancelled | passthrough. */
export interface DialogReply {
  text: string;
  state: DialogState;
  kind: string;
  question: string | null;
  options: string[];
  /** [номер вопроса, всего вопросов] — пока идёт анкета */
  progress: [number, number] | null;
}

export interface DownloadInfo {
  id: string;
  url: string;
  filename: string;
}

export interface TemplateInfo {
  id: string;
  name: string;
  placeholders: string[];
}

export interface Delivery {
  channel: string;
  available: boolean;
  reason: string;
}

export interface SessionDocument {
  filename: string;
  doc_type: { id: string | null; title: string | null; how: string };
  fields: { unit: string; title: string; found: boolean; value: string; corrected: boolean; doubt: string }[];
  warnings: string[];
  corrected: string[];
}

export interface AssistantState {
  session: "ok" | "new" | "expired";
  welcome: string;
  dialog: DialogInfo;
  document: SessionDocument | null;
  templates: TemplateInfo[];
  delivery: Delivery;
  llm: { enabled: boolean };
  downloads: DownloadInfo[];
  ticket_id: number | null;
}

export interface MessageResult {
  session: AssistantState["session"];
  reply: DialogReply;
  dialog: DialogInfo;
  answer: Answer | null;
  download: DownloadInfo | null;
}

export interface GenerateResult {
  download: DownloadInfo | null;
  duplicate: boolean;
  unfilled: string[];
  reply: DialogReply | null;
  dialog: DialogInfo;
}

/** Ответ на правку поля: карточка в виде результата проверки (без рамок) и ответ, пересчитанный по ней. */
export interface FieldCorrection {
  document: SessionDocument;
  fields: FieldResult[];
  answer: Answer | null;
}

export interface SubmissionPreview {
  delivery: Delivery;
  payload: { request: string; fields: Record<string, string> };
  excluded: string[];
  attachment: boolean;
  state: DialogState;
  already_sent: number | null;
  /** почему обращение не уходит в деканат (задолженности, неполные данные); пусто — можно отправить */
  blocked: string[];
  /** код статуса уже отправленного обращения (виден, пока жива сессия) */
  ticket_code: string;
  /** настроена ли почта для письма с номером и кодом */
  mail: { available: boolean; reason: string };
}

export interface SubmitResult {
  sent: boolean;
  duplicate?: boolean;
  ticket_id?: number;
  /** код для страницы «Статус обращения»: показывается студенту, в базе лежит только хэш */
  code?: string;
  /** письмо с номером и кодом: sent — ушло, failed — не получилось, disabled — почта не настроена, skipped — контакт не почта, none — контакта нет */
  mail?: { status: "sent" | "failed" | "disabled" | "skipped" | "none"; to?: string };
  excluded?: string[];
  message: string;
}

/** «Что дальше»: что принести и срок решения из Положения (null — в Положении об этом ничего нет). */
export interface NextSteps {
  documents: { text: string; sources: string[] } | null;
  deadline: { days: number; working: boolean; date: string; source: string; text: string } | null;
}

export interface TicketStatus {
  id: number;
  status: "new" | "in_progress" | "done" | "rejected";
  status_title: string;
  topic: string | null;
  created_at: string;
  updated_at: string;
  reply: string | null;
  reply_at: string | null;
}

/** Идентификатор режима «другой документ»: ответ по тексту самого документа без карточки полей. */
export const GENERIC_TYPE = "generic";
export const AUTO_TYPE = "auto";
