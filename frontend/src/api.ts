import type {
  ChatOut,
  ChatRequest,
  ChatResponse,
  DocumentContent,
  DocumentOut,
  MessageOut,
} from "./types";

const BASE = "/api";

async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep default */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function fetchDocuments(): Promise<DocumentOut[]> {
  const res = await fetch(`${BASE}/documents`);
  return handle<DocumentOut[]>(res);
}

export async function fetchDocumentContent(id: number): Promise<DocumentContent> {
  const res = await fetch(`${BASE}/documents/${id}/content`);
  return handle<DocumentContent>(res);
}

export async function uploadDocument(file: File): Promise<DocumentOut> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}/documents/upload`, { method: "POST", body: form });
  return handle<DocumentOut>(res);
}

export async function deleteDocument(id: number): Promise<void> {
  const res = await fetch(`${BASE}/documents/${id}`, { method: "DELETE" });
  await handle<{ deleted: number; status: string }>(res);
}

export async function deleteAllDocuments(): Promise<void> {
  const res = await fetch(`${BASE}/documents`, { method: "DELETE" });
  await handle<{ deleted: number; status: string }>(res);
}

export async function sendChat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  return handle<ChatResponse>(res);
}

export async function fetchChats(): Promise<ChatOut[]> {
  const res = await fetch(`${BASE}/chats`);
  return handle<ChatOut[]>(res);
}

export async function createChat(title?: string): Promise<ChatOut> {
  const res = await fetch(`${BASE}/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: title ?? null }),
  });
  return handle<ChatOut>(res);
}

export async function deleteChat(id: number): Promise<void> {
  const res = await fetch(`${BASE}/chats/${id}`, { method: "DELETE" });
  await handle<{ deleted: number; status: string }>(res);
}

export async function fetchChatMessages(id: number): Promise<MessageOut[]> {
  const res = await fetch(`${BASE}/chats/${id}/messages`);
  return handle<MessageOut[]>(res);
}
