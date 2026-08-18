import type {
  AdminReportList,
  AdminStats,
  AdminUser,
  AdminUserList,
  AgentEvent,
  AgentStep,
  AuthResponse,
  ChatOut,
  ChatRequest,
  ChatResponse,
  CompareResponse,
  CreatedDocument,
  DocumentContent,
  DocumentOut,
  MeStats,
  MessageOut,
  SourceRef,
  UserOut,
  UserRole,
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

export async function fetchMeStats(): Promise<MeStats> {
  const res = await fetch(`${BASE}/me/stats`, { headers: authHeaders() });
  return handle<MeStats>(res);
}

export async function changePassword(
  currentPassword: string,
  newPassword: string,
  passwordConfirm: string,
): Promise<void> {
  const res = await fetch(`${BASE}/auth/change-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({
      current_password: currentPassword,
      new_password: newPassword,
      password_confirm: passwordConfirm,
    }),
  });
  await handle<{ changed: boolean }>(res);
}

export async function deleteMe(): Promise<void> {
  const res = await fetch(`${BASE}/me`, { method: "DELETE", headers: authHeaders() });
  await handle<{ deleted: boolean }>(res);
}

export async function fetchDocuments(): Promise<DocumentOut[]> {
  const res = await fetch(`${BASE}/documents`, { headers: authHeaders() });
  return handle<DocumentOut[]>(res);
}

export async function fetchDocumentContent(id: number): Promise<DocumentContent> {
  const res = await fetch(`${BASE}/documents/${id}/content`, { headers: authHeaders() });
  return handle<DocumentContent>(res);
}

export async function fetchDocumentVersions(id: number): Promise<DocumentOut[]> {
  const res = await fetch(`${BASE}/documents/${id}/versions`, { headers: authHeaders() });
  return handle<DocumentOut[]>(res);
}

export async function compareDocuments(
  leftId: number,
  rightId: number,
): Promise<CompareResponse> {
  const res = await fetch(`${BASE}/documents/compare`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ left_id: leftId, right_id: rightId }),
  });
  return handle<CompareResponse>(res);
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
  const res = await fetch(`${BASE}/agent`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify(req),
  });
  return handle<ChatResponse>(res);
}

export interface StreamHandlers {
  onStep?: (step: AgentStep) => void;
  onDocumentCreated?: (doc: CreatedDocument & { download_url?: string }) => void;
  onFinal?: (content: string, sources?: SourceRef[]) => void;
  onError?: (message: string) => void;
}

/** Realtime agent run: POST /api/agent/stream, parsed as SSE frames. */
export async function streamAgent(req: ChatRequest, handlers: StreamHandlers): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${BASE}/agent/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...authHeaders() },
      body: JSON.stringify(req),
    });
  } catch (err) {
    handlers.onError?.(err instanceof Error ? err.message : "Сбой сети");
    return;
  }
  if (!res.ok || !res.body) {
    let detail = `HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ""}`;
    try {
      const body = await res.json();
      if (body.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* keep default */
    }
    handlers.onError?.(detail);
    return;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const dataLine = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!dataLine) continue;
      const json = dataLine.slice(5).trim();
      if (!json) continue;
      try {
        const evt = JSON.parse(json) as AgentEvent;
        if (evt.type === "agent_step" && evt.step_id) {
          handlers.onStep?.(evt as AgentStep);
        } else if (evt.type === "document_created") {
          handlers.onDocumentCreated?.({
            document_id: evt.document_id ?? 0,
            filename: evt.filename ?? "",
            file_type: evt.filename ? evt.filename.split(".").pop() ?? "" : "",
            download_url: evt.download_url,
          });
        } else if (evt.type === "final") {
          handlers.onFinal?.(evt.content ?? "", evt.sources);
        }
      } catch {
        /* ignore malformed frame */
      }
    }
  }
}

export async function downloadDocument(id: number, filename: string): Promise<void> {
  const res = await fetch(`${BASE}/documents/${id}/file`, { headers: authHeaders() });
  if (!res.ok) {
    throw new Error(`HTTP ${res.status}`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || `document-${id}`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
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

export async function renameChat(id: number, title: string): Promise<ChatOut> {
  const res = await fetch(`${BASE}/chats/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ title }),
  });
  return handle<ChatOut>(res);
}

export async function fetchChatMessages(id: number): Promise<MessageOut[]> {
  const res = await fetch(`${BASE}/chats/${id}/messages`, { headers: authHeaders() });
  return handle<MessageOut[]>(res);
}

export async function fetchAdminStats(): Promise<AdminStats> {
  const res = await fetch(`${BASE}/admin/stats`, { headers: authHeaders() });
  return handle<AdminStats>(res);
}

export interface AdminUsersParams {
  page: number;
  limit: number;
  search?: string;
}

export async function fetchAdminUsers(params: AdminUsersParams): Promise<AdminUserList> {
  const qs = new URLSearchParams({ page: String(params.page), limit: String(params.limit) });
  if (params.search) qs.set("search", params.search);
  const res = await fetch(`${BASE}/admin/users?${qs.toString()}`, { headers: authHeaders() });
  return handle<AdminUserList>(res);
}

export async function fetchAdminUser(id: number): Promise<AdminUser> {
  const res = await fetch(`${BASE}/admin/users/${id}`, { headers: authHeaders() });
  return handle<AdminUser>(res);
}

export async function patchAdminUserRole(id: number, role: UserRole): Promise<AdminUser> {
  const res = await fetch(`${BASE}/admin/users/${id}/role`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ role }),
  });
  return handle<AdminUser>(res);
}

export async function patchAdminUserStatus(id: number, isActive: boolean): Promise<AdminUser> {
  const res = await fetch(`${BASE}/admin/users/${id}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json", ...authHeaders() },
    body: JSON.stringify({ is_active: isActive }),
  });
  return handle<AdminUser>(res);
}

export async function deleteAdminUser(id: number): Promise<void> {
  const res = await fetch(`${BASE}/admin/users/${id}`, { method: "DELETE", headers: authHeaders() });
  await handle<{ deleted: boolean; user_id: number }>(res);
}

export async function fetchAdminUserReports(
  id: number,
  page: number,
  limit: number,
): Promise<AdminReportList> {
  const qs = new URLSearchParams({ page: String(page), limit: String(limit) });
  const res = await fetch(`${BASE}/admin/users/${id}/reports?${qs.toString()}`, {
    headers: authHeaders(),
  });
  return handle<AdminReportList>(res);
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