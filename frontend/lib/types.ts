export type User = {
  id: string;
  email: string;
  role: "user" | "admin";
  status: "pending" | "active";
  created_at?: string;
};

export type Chat = {
  id: string;
  title: string;
  mode?: string;
  share_token: string | null;
  created_at: string;
  updated_at: string;
};

export type Locator = {
  page?: number;
  row_from?: number;
  row_to?: number;
  t_start?: number;
  t_end?: number;
};

export type Citation = {
  ordinal: number;
  study_id: string | null;
  study_title: string;
  conducted_at: string | null;
  file_id: string | null;
  file_name: string;
  file_kind: "report_pdf" | "raw_csv" | "audio" | "video" | string;
  locator: Locator;
  snippet: string;
};

/** Кнопка-действие инструмента под сообщением ассистента (одноразовая). */
export type MessageAction = {
  id: string;
  label: string;
  data?: Record<string, unknown>;
};

export type Message = {
  id: number;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  citations: Citation[];
  actions?: MessageAction[] | null;
};

export type StudyFile = {
  id: string;
  kind: string;
  original_name: string;
  comment: string;
  size_bytes: number;
  status: "uploaded" | "processing" | "ready" | "error";
  error_message: string;
  meta: Record<string, unknown>;
  created_at: string;
};

export type Study = {
  id: string;
  title: string;
  conducted_at: string;
  comment: string;
  created_at: string;
  files_count: number;
  status: "processing" | "ready" | "error";
  files?: StudyFile[];
};

export type FileMeta = {
  id: string;
  kind: string;
  original_name: string;
  comment: string;
  mime: string;
  size_bytes: number;
  status: string;
  meta: { duration?: number; pages?: number; rows?: number };
  has_transcript: boolean;
  study: { id: string | null; title: string; conducted_at: string | null };
};

export type TranscriptSegment = { text: string; t_start: number; t_end: number };

export const KIND_LABELS: Record<string, string> = {
  report_pdf: "Отчет (PDF)",
  raw_csv: "Сырые данные (CSV)",
  audio: "Аудио интервью",
  video: "Видео интервью",
};
