export interface UserOut {
  id: number;
  email: string;
  role: string;
  created_at: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: string;
  user: UserOut;
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
}

export interface ChatResponse {
  chat_id: number;
  answer: string;
  sources: SourceRef[];
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
