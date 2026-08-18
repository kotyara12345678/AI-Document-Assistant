export interface UserOut {
  id: number;
  email: string;
  role: string;
  created_at: string;
}

export type UserRole = "user" | "moderator" | "admin";

export interface AdminUser {
  id: number;
  email: string;
  role: UserRole;
  created_at: string;
  last_active_at: string | null;
  is_active: boolean;
  is_deleted: boolean;
  reports_active: number;
}

export interface AdminUserList {
  items: AdminUser[];
  total: number;
  page: number;
  limit: number;
}

export type AdminReportStatus = "pending" | "reviewed" | "rejected" | "action_taken";

export interface AdminReport {
  id: number;
  reporter_email: string;
  reported_user_id: number;
  reason: string;
  description: string | null;
  status: AdminReportStatus;
  created_at: string;
  resolved_at: string | null;
  resolved_by_email: string | null;
}

export interface AdminReportList {
  items: AdminReport[];
  total: number;
  page: number;
  limit: number;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
}

export interface MeStats {
  user: UserOut;
  documents_total: number;
  chats_total: number;
  messages_total: number;
  tokens_used: number;
  last_active_at: string | null;
}

export interface DocumentOut {
  id: number;
  filename: string;
  original_filename: string;
  file_type: string;
  file_size: number;
  content_length: number;
  created_at: string;
}

export interface DocumentContent {
  id: number;
  original_filename: string;
  file_type: string;
  content_length: number;
  content: string;
}

export interface CompareDocumentRef {
  id: number;
  original_filename: string;
  file_type: string;
  content_length: number;
  created_at: string | null;
  source_file_id: number | null;
}

export type DiffKind = "equal" | "delete" | "insert" | "replace";

export interface DiffOperation {
  kind: DiffKind;
  left_start: number;
  left_end: number;
  right_start: number;
  right_end: number;
}

export interface CompareSummary {
  added_lines: number;
  removed_lines: number;
  changed_lines: number;
  unchanged_lines: number;
}

export interface CompareResponse {
  left: CompareDocumentRef;
  right: CompareDocumentRef;
  left_lines: string[];
  right_lines: string[];
  operations: DiffOperation[];
  summary: CompareSummary;
  equal: boolean;
  truncated: boolean;
  limit: number;
}

export interface SourceRef {
  document_id: number;
  filename: string;
  chunk_index: number;
  score: number;
  text: string;
}

export interface ChatRequest {
  chat_id?: number | null;
  question: string;
  document_id?: number | null;
  document_ids?: number[] | null;
  context_document_ids?: number[] | null;
}

export interface AgentToolCall {
  name: string;
  arguments: Record<string, unknown>;
}

export interface AgentStep {
  step_id: string;
  tool: string;
  message: string;
  status: "running" | "completed" | "error";
}

export interface AgentEvent {
  type: "agent_step" | "document_created" | "final";
  step_id?: string;
  status?: "running" | "completed" | "error";
  tool?: string;
  message?: string;
  content?: string;
  sources?: SourceRef[];
  document_id?: number;
  filename?: string;
  download_url?: string;
}

export interface CreatedDocument {
  document_id: number;
  filename: string;
  file_type: string;
}

export interface ChatResponse {
  chat_id: number;
  answer: string;
  sources: SourceRef[];
  tool_calls?: AgentToolCall[];
  created_documents?: CreatedDocument[];
}

export interface ChatOut {
  id: number;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface MessageOut {
  id: number;
  chat_id: number;
  role: string;
  content: string;
  created_at: string;
  document_id?: number | null;
  context_document_ids?: number[] | null;
}

export interface SearchResultItem {
  document_id: number;
  filename: string;
  chunk_index: number;
  text: string;
  score: number;
}

export interface AdminServiceStatus {
  database: string;
  qdrant: string;
  status: string;
}

export interface AdminErrorEntry {
  timestamp: string;
  status: number;
  path: string;
}

export interface AdminStats {
  services: AdminServiceStatus;
  users: { total: number; admins: number; new_last_24h: number };
  documents: { total: number; chunks: number; total_content_chars: number; new_last_24h: number };
  chats: { total: number; messages: number; new_last_24h: number };
  requests: { api_total: number; llm_requests: number; average_latency_ms: number };
  tokens: { total_tokens_used: number };
  errors: { total: number; status_buckets: Record<string, number>; recent: AdminErrorEntry[] };
  generated_at: string;
}
