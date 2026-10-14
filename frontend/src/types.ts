/** Типы ответов API (см. dekanat/models.py и dekanat/pipeline.py). Координаты рамок — в пикселях исходного снимка. */

export type Verdict = "ok" | "missing" | "no_answer";
export type FieldStatus = "found" | "partial" | "missing";

export interface Box {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
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
}

export interface SampleItem {
  file: string;
  title: string;
  question: string;
  doc_type?: string;
}

export interface Clause {
  number: string;
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
}

export interface SubmitResult {
  sent: boolean;
  duplicate?: boolean;
  ticket_id?: number;
  /** код для страницы «Статус обращения»: показывается студенту, в базе лежит только хэш */
  code?: string;
  excluded?: string[];
  message: string;
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
