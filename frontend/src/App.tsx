import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatOut, ChatResponse, DocumentContent, DocumentOut, MessageOut, SourceRef } from "./types";
import {
  createChat,
  deleteAllDocuments,
  deleteChat,
  deleteDocument,
  fetchChatMessages,
  fetchChats,
  fetchDocumentContent,
  fetchDocuments,
  sendChat,
} from "./api";
import UploadDropzone from "./components/UploadDropzone";
import FileViewer from "./components/FileViewer";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: SourceRef[];
  error?: boolean;
}

const THEME_KEY = "docsearch-theme";
let localMsgId = 0;

function nextLocalId(): number {
  // Negative ids never collide with server-persisted message ids.
  return -(++localMsgId);
}

export default function App() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [chats, setChats] = useState<ChatOut[]>([]);
  const [activeChatId, setActiveChatId] = useState<number | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [docsLoading, setDocsLoading] = useState(true);
  const [chatsLoading, setChatsLoading] = useState(true);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [theme, setTheme] = useState<string>(() => localStorage.getItem(THEME_KEY) || "dark");

  const [viewer, setViewer] = useState<DocumentContent | null>(null);
  const [viewerHighlights, setViewerHighlights] = useState<string[]>([]);
  const [viewerLoading, setViewerLoading] = useState(false);

  const noticeTimer = useRef<number | null>(null);

  const flashNotice = useCallback((msg: string) => {
    setNotice(msg);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 4000);
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  const loadDocuments = useCallback(async () => {
    try {
      setDocuments(await fetchDocuments());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setDocsLoading(false);
    }
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats(await fetchChats());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load chats");
    }
  }, []);

  const selectChat = useCallback(
    async (chatId: number) => {
      setActiveChatId(chatId);
      localStorage.setItem("docsearch-active-chat", String(chatId));
      setMessages([]);
      setError(null);
      setMessagesLoading(true);
      try {
        const rows = await fetchChatMessages(chatId);
        setMessages(
          rows.map((m: MessageOut) => ({
            id: m.id,
            role: m.role as Message["role"],
            text: m.content,
          }))
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load messages");
      } finally {
        setMessagesLoading(false);
      }
    },
    []
  );

  // Initial load: documents, chats and the last active chat.
  useEffect(() => {
    void loadDocuments();
    let cancelled = false;
    void (async () => {
      try {
        const list = await fetchChats();
        if (cancelled) return;
        setChats(list);
        if (list.length > 0) {
          const stored = localStorage.getItem("docsearch-active-chat");
          const preferred = stored ? list.find((c) => c.id === Number(stored)) : undefined;
          const target = preferred ?? list[0];
          setActiveChatId(target.id);
          const rows = await fetchChatMessages(target.id);
          if (cancelled) return;
          setMessages(
            rows.map((m: MessageOut) => ({
              id: m.id,
              role: m.role as Message["role"],
              text: m.content,
            }))
          );
        } else {
          // No chats yet: create the first one automatically.
          const chat = await createChat();
          if (cancelled) return;
          setChats([chat]);
          setActiveChatId(chat.id);
          localStorage.setItem("docsearch-active-chat", String(chat.id));
        }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load chats");
      } finally {
        if (!cancelled) setChatsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadDocuments]);

  const newChat = useCallback(async () => {
    try {
      const chat = await createChat();
      setChats((prev) => [chat, ...prev]);
      setActiveChatId(chat.id);
      localStorage.setItem("docsearch-active-chat", String(chat.id));
      setMessages([]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create chat");
    }
  }, []);

  const removeChat = useCallback(
    async (chatId: number) => {
      try {
        await deleteChat(chatId);
        const next = chats.filter((c) => c.id !== chatId);
        setChats(next);
        if (activeChatId === chatId) {
          if (next.length > 0) {
            await selectChat(next[0].id);
          } else {
            setActiveChatId(null);
            setMessages([]);
            localStorage.removeItem("docsearch-active-chat");
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete chat");
      }
    },
    [chats, activeChatId, selectChat]
  );

  const openDocument = useCallback(async (id: number, highlights: string[] = []) => {
    setViewerLoading(true);
    setViewerHighlights(highlights);
    try {
      setViewer(await fetchDocumentContent(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load document content");
      setViewer(null);
    } finally {
      setViewerLoading(false);
    }
  }, []);

  const closeViewer = useCallback(() => {
    setViewer(null);
    setViewerHighlights([]);
  }, []);

  const onUploaded = useCallback(
    (doc: DocumentOut) => {
      setDocuments((prev) => [doc, ...prev]);
      setError(null);
      void openDocument(doc.id);
      flashNotice(`Document "${doc.original_filename}" uploaded and indexed.`);
    },
    [openDocument, flashNotice]
  );

  const removeDocument = useCallback(
    async (doc: DocumentOut) => {
      if (!window.confirm(`Delete "${doc.original_filename}" and its index?`)) return;
      try {
        await deleteDocument(doc.id);
        setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
        if (viewer?.id === doc.id) closeViewer();
        flashNotice(`Document "${doc.original_filename}" deleted.`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete document");
      }
    },
    [viewer, closeViewer, flashNotice]
  );

  const clearAllDocuments = useCallback(async () => {
    if (!window.confirm("Delete ALL documents and their indexes?")) return;
    try {
      await deleteAllDocuments();
      setDocuments([]);
      closeViewer();
      flashNotice("All documents deleted.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to delete documents");
    }
  }, [closeViewer, flashNotice]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      if (activeChatId == null) {
        setError("No active chat. Please create a chat first.");
        return;
      }

      const chatId = activeChatId;
      const userMsg: Message = { id: nextLocalId(), role: "user", text: trimmed };
      setMessages((prev) => [...prev, userMsg]);
      setInput("");
      setLoading(true);

      try {
        const data: ChatResponse = await sendChat({ chat_id: chatId, question: trimmed });
        setMessages((prev) => [
          ...prev,
          { id: nextLocalId(), role: "assistant", text: data.answer, sources: data.sources },
        ]);
        // The backend may have titled this chat after its first question.
        void refreshChats();
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: nextLocalId(),
            role: "assistant",
            text: err instanceof Error ? err.message : "Something went wrong",
            error: true,
          },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [loading, activeChatId, refreshChats]
  );

  const openSource = useCallback(
    (s: SourceRef) => {
      void openDocument(s.document_id, [s.text]);
    },
    [openDocument]
  );

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar__header">
          <span className="sidebar__logo">📄</span>
          <span className="sidebar__brand">
            Doc<span className="sidebar__brand-accent">Search</span>
          </span>
          <button
            className="theme-toggle"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            {theme === "dark" ? "☀️" : "🌙"}
          </button>
        </div>

        <div className="sidebar__section sidebar__section--chats">
          <div className="sidebar__section-title-row">
            <div className="sidebar__section-title">Chats</div>
            <button className="btn btn--new-chat" onClick={() => void newChat()}>
              ＋ New chat
            </button>
          </div>
        </div>
        <div className="sidebar__list sidebar__list--chats">
          {chatsLoading ? (
            <div className="empty">Loading chats…</div>
          ) : chats.length === 0 ? (
            <div className="empty">No chats yet.</div>
          ) : (
            chats.map((chat) => (
              <div
                key={chat.id}
                className={`chat-item ${activeChatId === chat.id ? "chat-item--active" : ""}`}
                onClick={() => void selectChat(chat.id)}
              >
                <span className="chat-item__icon">💬</span>
                <span className="chat-item__title">{chat.title}</span>
                <button
                  className="chat-item__delete"
                  title="Delete chat"
                  onClick={(e) => {
                    e.stopPropagation();
                    void removeChat(chat.id);
                  }}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>

        <UploadDropzone onUploaded={onUploaded} onError={setError} />
        <div className="sidebar__section">
          <div className="sidebar__section-title-row">
            <div className="sidebar__section-title">Your documents</div>
            {documents.length > 0 && (
              <button className="btn btn--clear" onClick={() => void clearAllDocuments()}>
                Clear all
              </button>
            )}
          </div>
        </div>
        <div className="sidebar__list">
          {docsLoading ? (
            <div className="empty">Loading documents…</div>
          ) : documents.length === 0 ? (
            <div className="empty">No documents yet. Upload your first file.</div>
          ) : (
            documents.map((doc) => (
              <div
                key={doc.id}
                className={`doc-item ${viewer?.id === doc.id ? "doc-item--active" : ""}`}
                onClick={() => void openDocument(doc.id)}
              >
                <div className="doc-item__icon">
                  {doc.file_type === "pdf" ? "📕" : doc.file_type === "docx" ? "📘" : "📄"}
                </div>
                <div className="doc-item__body">
                  <div className="doc-item__name">{doc.original_filename}</div>
                  <div className="doc-item__meta">
                    {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)}
                  </div>
                </div>
                <button
                  className="doc-item__delete"
                  title="Delete document"
                  onClick={(e) => {
                    e.stopPropagation();
                    void removeDocument(doc);
                  }}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>
      </aside>

      <main className="chat">
        <div className="chat__header">
          <div className="chat__header-title">
            {chats.find((c) => c.id === activeChatId)?.title ?? "Ask anything"}
          </div>
          <div className="chat__header-sub">Searches across all {documents.length} documents</div>
        </div>

        <div className="chat__messages">
          {messagesLoading ? (
            <div className="chat__empty">
              <div className="chat__empty-icon">💬</div>
              <div className="chat__empty-sub">Loading conversation…</div>
            </div>
          ) : messages.length === 0 ? (
            <div className="chat__empty">
              <div className="chat__empty-icon">💬</div>
              <div className="chat__empty-title">Ask a question in your own words</div>
              <div className="chat__empty-sub">
                Upload documents, then ask. Matched fragments are highlighted in the files.
              </div>
            </div>
          ) : (
            messages.map((m) => (
              <div key={m.id} className={`msg msg--${m.role}`}>
                <div className={`msg__bubble ${m.error ? "msg__bubble--error" : ""}`}>{m.text}</div>
                {m.sources && m.sources.length > 0 && (
                  <div className="sources">
                    <div className="sources__title">
                      Sources · {m.sources.length} {m.sources.length === 1 ? "match" : "matches"} — click to view
                    </div>
                    {m.sources.map((s, i) => (
                      <button key={i} className="source" onClick={() => openSource(s)}>
                        <span className="source__file">
                          {s.filename}
                          <span className="source__chunk">chunk {s.chunk_index}</span>
                        </span>
                        <span className="source__score">
                          {(s.score * 100).toFixed(0)}% match
                        </span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            ))
          )}
          {loading && (
            <div className="msg msg--assistant">
              <div className="msg__bubble msg__bubble--typing">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
              </div>
            </div>
          )}
        </div>

        {notice && <div className="banner banner--ok">{notice}</div>}

        {error && (
          <div className="banner banner--error" onClick={() => setError(null)}>
            {error}
          </div>
        )}

        <form
          className="chat__input"
          onSubmit={(e) => {
            e.preventDefault();
            void sendMessage(input);
          }}
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask a question about your documents…"
            disabled={loading}
          />
          <button type="submit" disabled={loading || !input.trim()}>
            {loading ? "…" : "Send"}
          </button>
        </form>
      </main>

      <FileViewer
        doc={viewer}
        highlights={viewerHighlights}
        loading={viewerLoading}
        onClose={closeViewer}
      />
    </div>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
