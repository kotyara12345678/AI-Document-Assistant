import type {
  AdminStats,
  AuthResponse,
  ChatOut,
  ChatRequest,
  ChatResponse,
  DocumentContent,
  DocumentOut,
  MessageOut,
  UserOut,
} from "./types";

const BASE = "/api";

const TOKEN_KEY = "docsearch-token";

export function getToken(): string | null {
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token);
    else localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable */
  }
}

function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401 && !res.url.endsWith("/auth/login")) {
    setToken(null);
  }
  if (!res.ok) {
    let detail = `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}`;
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

export async function register(email: string, password: string, passwordConfirm: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password, password_confirm: passwordConfirm }),
  });
  return handle<AuthResponse>(res);
}

export async function login(email: string, password: string): Promise<AuthResponse> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return handle<AuthResponse>(res);
}

export async function fetchMe(): Promise<UserOut> {
  const res = await fetch(`${BASE}/auth/me`, { headers: authHeaders() });
  return handle<UserOut>(res);
}

export async function fetchDocuments(): Promise<DocumentOut[]> {
  const res = await fetch(`${BASE}/documents`, { headers: authHeaders() });
  return handle<DocumentOut[]>(res);
}

export async function fetchDocumentContent(id: number): Promise<DocumentContent> {
  const res = await fetch(`${BASE}/documents/${id}/content`, { headers: authHeaders() });
  return handle<DocumentContent>(res);
}

export async function uploadDocuments(files: File[]): Promise<DocumentOut[]> {
  const form = new FormData();
  for (const file of files) form.append("file", file);
  const res = await fetch(`${BASE}/documents/upload`, { method: "POST", body: form, headers: authHeaders() });
  return handle<DocumentOut[]>(res);
}

export async function deleteDocument(id: number): Promise<void> {
  const res = await fetch(`${BASE}/documents/${id}`, { method: "DELETE", headers: authHeaders() });
  await handle<{ deleted: number; status: string }>(res);
}

export async function deleteAllDocuments(): Promise<void> {
  const res = await fetch(`${BASE}/documents`, { method: "DELETE", headers: authHeaders() });
  await handle<{ deleted: number; status: string }>(res);
}

export async function sendChat(req: ChatRequest): Promise<ChatResponse> {
  const res = await fetch(`${BASE}/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(req),
  });
  return handle<ChatResponse>(res);
}

export async function fetchChats(): Promise<ChatOut[]> {
  const res = await fetch(`${BASE}/chats`, { headers: authHeaders() });
  return handle<ChatOut[]>(res);
}

export async function createChat(title?: string): Promise<ChatOut> {
  const res = await fetch(`${BASE}/chats`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ title: title ?? null }),
  });
  return handle<ChatOut>(res);
}

export async function deleteChat(id: number): Promise<void> {
  const res = await fetch(`${BASE}/chats/${id}`, { method: "DELETE", headers: authHeaders() });
  await handle<{ deleted: number; status: string }>(res);
}

export async function fetchChatMessages(id: number): Promise<MessageOut[]> {
  const res = await fetch(`${BASE}/chats/${id}/messages`, { headers: authHeaders() });
  return handle<MessageOut[]>(res);
}

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await fetch(`${BASE}/admin/stats`, { headers: authHeaders() });
  return handle<AdminStats>(res);
}

export function documentFileUrl(id: number): string {
  return `${BASE}/documents/${id}/file`;
}

export function documentFileSource(id: number): { url: string; httpHeaders: Record<string, string> } {
  const token = getToken();
  return {
    url: documentFileUrl(id),
    httpHeaders: token ? { Authorization: `Bearer ${token}` } : {},
  };
}