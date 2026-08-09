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
