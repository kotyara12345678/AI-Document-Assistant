import { memo, useCallback, useEffect, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { AgentStep, ChatOut, CreatedDocument, DocumentContent, DocumentOut, MessageOut, SourceRef, UserOut } from "./types";
import {
  createChat,
  deleteAllDocuments,
  deleteChat,
  deleteDocument,
  downloadDocument,
  fetchChatMessages,
  fetchChats,
  fetchDocumentContent,
  fetchDocuments,
  fetchMe,
  getToken,
  renameChat,
  setToken,
  streamAgent,
  uploadDocuments,
} from "./api";
import UploadDropzone from "./components/UploadDropzone";
import FileViewer from "./components/FileViewer";
import AdminPanel from "./components/AdminPanel";
import ProfilePanel from "./components/ProfilePanel";
import LandingFlow from "./components/LandingFlow";
import UploadWarning from "./components/UploadWarning";
import ComparePanel from "./components/ComparePanel";
import { hasSeenUploadWarning, markUploadWarningSeen } from "./consent";
import CopyableBlock, { extractCodeBlock } from "./codeBlock";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: SourceRef[];
  agentSteps?: AgentStep[];
  createdDocuments?: CreatedDocument[];
  documentId?: number;
  contextDocumentIds?: number[] | null;
  error?: boolean;
}

const THEME_KEY = "docsearch-theme";
let localMsgId = 0;

function nextLocalId(): number {
  // Negative ids never collide with server-persisted message ids.
  return -(++localMsgId);
}

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

function fileIcon(fileType: string): string {
  switch (fileType) {
    case "pdf":
      return "📕";
    case "docx":
      return "📘";
    case "md":
      return "📝";
    default:
      return "📄";
  }
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function App() {
  const [user, setUser] = useState<UserOut | null>(null);
  const [authChecking, setAuthChecking] = useState(true);
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
  const [theme, setTheme] = useState<string>(() => localStorage.getItem(THEME_KEY) || "light");

  const [viewer, setViewer] = useState<DocumentContent | null>(null);
  const [viewerHighlights, setViewerHighlights] = useState<string[]>([]);
  const [viewerLoading, setViewerLoading] = useState(false);
  const [showAdmin, setShowAdmin] = useState(false);
  const [showProfile, setShowProfile] = useState(false);
  // Compare mode: which document is preselected on the left (null = closed).
  const [compareDocId, setCompareDocId] = useState<number | null>(null);
  // Docs list "⋯" menu: which document's actions menu is open (null = closed).
  const [docMenuFor, setDocMenuFor] = useState<number | null>(null);
  // Chats list "⋯" menu: which chat's actions menu is open (null = closed).
  const [chatMenuFor, setChatMenuFor] = useState<number | null>(null);
  // Mobile-only drawer (left panel becomes a tab opened via the top-right button).
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Documents the user has pinned as context for the next message (UI chips).
  const [contextDocs, setContextDocs] = useState<number[]>([]);
  // Files awaiting confirmation of the first-upload warning.
  const [warningPending, setWarningPending] = useState<File[] | null>(null);

  const noticeTimer = useRef<number | null>(null);
  const composerFileRef = useRef<HTMLInputElement | null>(null);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const [showScrollBottom, setShowScrollBottom] = useState(false);
  const showScrollBottomRef = useRef(false);
  const stickToBottomRef = useRef(true);
  const prevMessagesLenRef = useRef(0);

  // Mobile drawer swipe-to-close: press on the panel and drag left to hide it.
  const sidebarElRef = useRef<HTMLElement | null>(null);
  const dragStartRef = useRef<{ x: number; y: number; t: number } | null>(null);

  const flashNotice = useCallback((msg: string) => {
    setNotice(msg);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 4000);
  }, []);

  const handleMessagesScroll = useCallback(() => {
    const el = messagesRef.current;
    if (!el) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    stickToBottomRef.current = nearBottom;
    if (showScrollBottomRef.current === nearBottom) return;
    showScrollBottomRef.current = nearBottom;
    setShowScrollBottom(!nearBottom);
  }, []);

  const scrollMessagesToBottom = useCallback(() => {
    stickToBottomRef.current = true;
    const el = messagesRef.current;
    if (el) el.scrollTo({ top: el.scrollHeight, behavior: "smooth" });
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
    setChats([]);
    setMessages([]);
    setDocuments([]);
    setActiveChatId(null);
    setError(null);
    setNotice(null);
    setShowAdmin(false);
    setShowProfile(false);
    localStorage.removeItem("docsearch-active-chat");
  }, []);

  // Restore the session from a stored token, or show the auth screen.
  useEffect(() => {
    let cancelled = false;
    if (!getToken()) {
      setAuthChecking(false);
      return;
    }
    fetchMe()
      .then((me) => {
        if (!cancelled) setUser(me);
      })
      .catch(() => {
        if (!cancelled) {
          setToken(null);
        }
      })
      .finally(() => {
        if (!cancelled) setAuthChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem(THEME_KEY, theme);
  }, [theme]);

  // Keep the view pinned to the bottom while streaming new messages,
  // unless the user has scrolled up to read history.
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    const grew = messages.length > prevMessagesLenRef.current;
    prevMessagesLenRef.current = messages.length;
    if (grew && stickToBottomRef.current) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages]);

  // Close the mobile drawer with Escape.
  useEffect(() => {
    if (!sidebarOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSidebarOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sidebarOpen]);

  // --- Mobile drawer swipe-to-close (drag the panel left) ---
  const clearSidebarDrag = useCallback(() => {
    dragStartRef.current = null;
    const el = sidebarElRef.current;
    if (!el) return;
    el.style.transition = "";
    el.style.transform = "";
  }, []);

  const onSidebarPointerDown = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      if (!sidebarOpen) return;
      const el = e.currentTarget;
      sidebarElRef.current = el;
      dragStartRef.current = { x: e.clientX, y: e.clientY, t: Date.now() };
      try {
        el.setPointerCapture(e.pointerId);
      } catch {
        /* not critical */
      }
      el.style.transition = "none";
    },
    [sidebarOpen]
  );

  const onSidebarPointerMove = useCallback((e: ReactPointerEvent<HTMLElement>) => {
    const start = dragStartRef.current;
    if (!start) return;
    const el = sidebarElRef.current;
    if (!el) return;
    const dx = e.clientX - start.x;
    const dy = e.clientY - start.y;
    // Follow the finger only on a leftward horizontal swipe, so that
    // vertical scrolling inside the drawer keeps working normally.
    if (dx < 0 && Math.abs(dx) > Math.abs(dy)) {
      el.style.transform = `translateX(${dx}px)`;
    }
  }, []);

  const finishSidebarDrag = useCallback(
    (e: ReactPointerEvent<HTMLElement>) => {
      const start = dragStartRef.current;
      const el = sidebarElRef.current;
      dragStartRef.current = null;
      if (!start || !el) return;
      const dx = e.clientX - start.x;
      const dt = Date.now() - start.t;
      const swipeLeft = dx <= -72 || (dx <= -40 && dt < 300);
      el.style.transition = "transform 0.28s ease";
      if (swipeLeft) {
        // Animate fully off-screen, then switch state so the class takes over.
        el.style.transform = "translateX(-102%)";
        window.setTimeout(() => {
          setSidebarOpen(false);
          clearSidebarDrag();
        }, 300);
      } else {
        // Snap back to the open position.
        el.style.transform = "translateX(0)";
        window.setTimeout(() => clearSidebarDrag(), 300);
      }
      try {
        el.releasePointerCapture(e.pointerId);
      } catch {
        /* not critical */
      }
    },
    [clearSidebarDrag]
  );

  const loadDocuments = useCallback(async () => {
    try {
      setDocuments(await fetchDocuments());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить документы");
    } finally {
      setDocsLoading(false);
    }
  }, []);

  const refreshChats = useCallback(async () => {
    try {
      setChats(await fetchChats());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось загрузить чаты");
    }
  }, []);

  const selectChat = useCallback(
    async (chatId: number) => {
      setSidebarOpen(false);
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
              documentId: m.document_id ?? undefined,
              contextDocumentIds: m.context_document_ids ?? undefined,
            }))
          );
        } catch (err) {
          setError(err instanceof Error ? err.message : "Не удалось загрузить сообщения");
        } finally {
          setMessagesLoading(false);
        }
      },
    []
  );

  // Initial load: documents, chats and the last active chat.
  useEffect(() => {
    if (!user) return;
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
              documentId: m.document_id ?? undefined,
              contextDocumentIds: m.context_document_ids ?? undefined,
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
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось загрузить чаты");
      } finally {
        if (!cancelled) setChatsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [loadDocuments, user]);

  const newChat = useCallback(async () => {
    setSidebarOpen(false);
    try {
      const chat = await createChat();
      setChats((prev) => [chat, ...prev]);
      setActiveChatId(chat.id);
      localStorage.setItem("docsearch-active-chat", String(chat.id));
      setMessages([]);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось создать чат");
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
        setError(err instanceof Error ? err.message : "Не удалось удалить чат");
      }
    },
    [chats, activeChatId, selectChat]
  );

  const renameChatNow = useCallback(
    async (chat: ChatOut) => {
      setChatMenuFor(null);
      const next = window.prompt("Название чата:", chat.title);
      if (next === null) return;
      const title = next.trim();
      if (!title || title === chat.title) return;
      try {
        const updated = await renameChat(chat.id, title);
        setChats((prev) => prev.map((c) => (c.id === updated.id ? updated : c)));
        flashNotice("Чат переименован.");
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось переименовать чат");
      }
    },
    [flashNotice]
  );

  const openDocument = useCallback(async (id: number, highlights: string[] = []) => {
    setSidebarOpen(false);
    // Document preview is desktop-only for now (mobile shows the file list only).
    if (window.matchMedia("(max-width: 900px)").matches) return;
    setViewerLoading(true);
    setViewerHighlights(highlights);
    try {
      setViewer(await fetchDocumentContent(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось открыть содержимое документа");
      setViewer(null);
    } finally {
      setViewerLoading(false);
    }
  }, []);

  const closeViewer = useCallback(() => {
    setViewer(null);
    setViewerHighlights([]);
  }, []);

  const openCompare = useCallback((id: number) => {
    setSidebarOpen(false);
    setCompareDocId(id);
  }, []);

  const closeCompare = useCallback(() => {
    setCompareDocId(null);
  }, []);

  const toggleContextDoc = useCallback((id: number) => {
    setContextDocs((prev) =>
      prev.includes(id) ? prev.filter((d) => d !== id) : [...prev, id]
    );
  }, []);

  const removeContextDoc = useCallback((id: number) => {
    setContextDocs((prev) => prev.filter((d) => d !== id));
  }, []);

  const onUploaded = useCallback(
    (doc: DocumentOut) => {
      setDocuments((prev) => [doc, ...prev]);
      setError(null);
      void openDocument(doc.id);
      flashNotice(`Документ «${doc.original_filename}» загружен и проиндексирован.`);
    },
    [openDocument, flashNotice]
  );

  const runUpload = useCallback(
    async (files: File[]) => {
      try {
        const docs = await uploadDocuments(files);
        docs.forEach((doc) => onUploaded(doc));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось загрузить документы");
      }
    },
    [onUploaded]
  );

  const requestUpload = useCallback(
    (files: FileList | null) => {
      if (!files || files.length === 0) return;
      const selected = Array.from(files);
      if (hasSeenUploadWarning()) {
        void runUpload(selected);
      } else {
        setWarningPending(selected);
      }
    },
    [runUpload]
  );

  const confirmUploadWarning = useCallback(() => {
    markUploadWarningSeen();
    const pending = warningPending;
    setWarningPending(null);
    if (pending) void runUpload(pending);
  }, [warningPending, runUpload]);

  const removeDocument = useCallback(
    async (doc: DocumentOut) => {
      if (!window.confirm(`Удалить «${doc.original_filename}» и его индекс?`)) return;
      try {
        await deleteDocument(doc.id);
        setDocuments((prev) => prev.filter((d) => d.id !== doc.id));
        if (viewer?.id === doc.id) closeViewer();
        flashNotice(`Документ «${doc.original_filename}» удалён.`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось удалить документ");
      }
    },
    [viewer, closeViewer, flashNotice]
  );

  const saveDocument = useCallback(
    async (doc: DocumentOut) => {
      setDocMenuFor(null);
      setError(null);
      try {
        await downloadDocument(doc.id, doc.original_filename);
        flashNotice(`«${doc.original_filename}» скачан.`);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось скачать документ");
      }
    },
    [flashNotice]
  );

  const clearAllDocuments = useCallback(async () => {
    if (!window.confirm("Удалить ВСЕ документы и их индексы?")) return;
    try {
      await deleteAllDocuments();
      setDocuments([]);
      closeViewer();
      flashNotice("Все документы удалены.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось удалить документы");
    }
  }, [closeViewer, flashNotice]);

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      if (activeChatId == null) {
        setError("Нет активного чата. Сначала создайте чат.");
        return;
      }

      const chatId = activeChatId;
      const userMsg: Message = { id: nextLocalId(), role: "user", text: trimmed };
      const assistantId = nextLocalId();
      const assistantMsg: Message = { id: assistantId, role: "assistant", text: "", agentSteps: [] };
      // Sending pins the view back to the bottom even if the user had
      // scrolled up to read history; the [messages] effect below keeps it
      // pinned while the assistant answer streams in.
      stickToBottomRef.current = true;
      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setInput("");
      setLoading(true);

      try {
        await streamAgent(
          {
            chat_id: chatId,
            question: trimmed,
            context_document_ids: contextDocs.length ? [...contextDocs] : null,
          },
          {
            onStep: (step) => {
              setMessages((prev) =>
                prev.map((m) => {
                  if (m.id !== assistantId) return m;
                  const steps = m.agentSteps ? [...m.agentSteps] : [];
                  const idx = steps.findIndex((s) => s.step_id === step.step_id);
                  if (idx >= 0) steps[idx] = step;
                  else steps.push(step);
                  return { ...m, agentSteps: steps };
                })
              );
            },
            onDocumentCreated: (doc) => {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId
                    ? {
                        ...m,
                        createdDocuments: [
                          ...(m.createdDocuments || []),
                          {
                            document_id: doc.document_id,
                            filename: doc.filename,
                            file_type: doc.file_type,
                          },
                        ],
                      }
                    : m
                )
              );
            },
            onFinal: (content, sources) => {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, text: content, sources: sources || m.sources } : m
                )
              );
            },
            onError: (msg) => {
              setMessages((prev) =>
                prev.map((m) =>
                  m.id === assistantId ? { ...m, text: msg, error: true } : m
                )
              );
            },
          }
        );
        // The backend may have titled this chat after its first question.
        setContextDocs([]);
        void refreshChats();
      } catch (err) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, text: err instanceof Error ? err.message : "Что-то пошло не так", error: true }
              : m
          )
        );
      } finally {
        setLoading(false);
      }
    },
    [loading, activeChatId, refreshChats, contextDocs]
  );

  const openSource = useCallback(
    (s: SourceRef) => {
      void openDocument(s.document_id, [s.text]);
    },
    [openDocument]
  );

  if (authChecking) {
    return (
      <div className="auth">
        <div className="auth__loading">
          <div className="auth__spinner" />
          <span>Загружаем сессию…</span>
        </div>
      </div>
    );
  }

  if (!user) {
    return <LandingFlow onAuthed={setUser} />;
  }

  return (
    <div className="layout">
      <aside
        className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}
        onPointerDown={onSidebarPointerDown}
        onPointerMove={onSidebarPointerMove}
        onPointerUp={finishSidebarDrag}
        onPointerCancel={clearSidebarDrag}
      >
        <div className="sidebar__header">
          <span className="sidebar__brand">ADA</span>
          <div className="sidebar__actions">
            <button
              className="sidebar__close-btn"
              onClick={() => setSidebarOpen(false)}
              title="Закрыть меню"
              aria-label="Закрыть меню"
            >
              ✕
            </button>
            <button
              className="theme-toggle"
              onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
              title={theme === "dark" ? "Переключить на светлую тему" : "Переключить на тёмную тему"}
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
            <button className="logout-btn" onClick={logout} title={`Выйти (${user.email})`}>
              Выйти
            </button>
          </div>
        </div>

        <button
          type="button"
          className="sidebar__user"
          onClick={() => {
            setSidebarOpen(false);
            setShowAdmin(false);
            setShowProfile(true);
          }}
          title="Личный кабинет"
        >
          <span className="sidebar__user-avatar">{user.email.slice(0, 1).toUpperCase()}</span>
          <div className="sidebar__user-info">
            <div className="sidebar__user-name" title={user.email}>
              {user.email}
            </div>
            <div className="sidebar__user-meta">
              {user.role === "admin"
                ? "Администратор"
                : user.role === "moderator"
                  ? "Модератор"
                  : "Пользователь"}
            </div>
          </div>
        </button>

        {user.role === "admin" && (
          <div className="sidebar__section">
            <button
              className="btn--admin"
              onClick={() => {
                setSidebarOpen(false);
                setShowProfile(false);
                setShowAdmin((v) => !v);
              }}
            >
              {showAdmin ? "◀ К чату" : "⚙ Админ-панель"}
            </button>
          </div>
        )}

        <div className="sidebar__section sidebar__section--scroll">
          <div className="sidebar__section-title">Чаты</div>
          <button className="btn--new-chat" onClick={() => void newChat()}>
            ＋ Новый чат
          </button>
          {chatsLoading ? (
            <div className="empty">Загружаем чаты…</div>
          ) : chats.length === 0 ? (
            <div className="empty">Чатов пока нет.</div>
          ) : (
            chats.map((chat) => (
              <div
                key={chat.id}
                className={`chat-item ${activeChatId === chat.id ? "chat-item--active" : ""}`}
                onClick={() => void selectChat(chat.id)}
              >
                <div className="chat-item__actions">
                  {chatMenuFor === chat.id && (
                    <div className="doc-menu-backdrop" onClick={() => setChatMenuFor(null)} />
                  )}
                  <button
                    className="chat-item__menu"
                    title="Действия с чатом"
                    aria-label="Действия с чатом"
                    onClick={(e) => {
                      e.stopPropagation();
                      setChatMenuFor((cur) => (cur === chat.id ? null : chat.id));
                    }}
                  >
                    ⋮
                  </button>
                  {chatMenuFor === chat.id && (
                    <div className="doc-menu">
                      <button
                        className="doc-menu__item"
                        onClick={(e) => {
                          e.stopPropagation();
                          void renameChatNow(chat);
                        }}
                      >
                        Переименовать
                      </button>
                      <button
                        className="doc-menu__item doc-menu__item--danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          setChatMenuFor(null);
                          void removeChat(chat.id);
                        }}
                      >
                        Удалить чат
                      </button>
                    </div>
                  )}
                </div>
                <span className="chat-item__title">{chat.title}</span>
                <span className="chat-item__meta">{new Date(chat.updated_at).toLocaleDateString()}</span>
              </div>
            ))
          )}
        </div>

        <UploadDropzone onUploaded={onUploaded} onError={setError} />

        <div className="sidebar__section sidebar__section--scroll">
          <div className="sidebar__section-title-row">
            <div className="sidebar__section-title">Документы</div>
            {documents.length > 0 && (
              <button className="btn--link" onClick={() => void clearAllDocuments()}>
                Очистить всё
              </button>
            )}
          </div>
          {docsLoading ? (
            <div className="empty">Загружаем документы…</div>
          ) : documents.length === 0 ? (
            <div className="empty">Документов пока нет. Загрузите первый файл.</div>
          ) : (
            documents.map((doc) => (
              <div
                key={doc.id}
                className={`doc-item ${viewer?.id === doc.id ? "doc-item--active" : ""} ${
                  contextDocs.includes(doc.id) ? "doc-item--context" : ""
                }`}
                onClick={() => void openDocument(doc.id)}
                onDoubleClick={() => toggleContextDoc(doc.id)}
                title="Открыть (один клик) · закрепить как контекст (двойной клик)"
              >
                <div className="doc-item__icon">{fileIcon(doc.file_type)}</div>
                <div className="doc-item__body">
                  <div className="doc-item__title">{doc.original_filename}</div>
                  <div className="doc-item__meta">
                    {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)}
                  </div>
                </div>
                <div className="doc-item__actions">
                  {docMenuFor === doc.id && (
                    <div className="doc-menu-backdrop" onClick={() => setDocMenuFor(null)} />
                  )}
                  <button
                    className="doc-item__menu"
                    title="Действия с документом"
                    aria-label="Действия с документом"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDocMenuFor((cur) => (cur === doc.id ? null : doc.id));
                    }}
                  >
                    ⋮
                  </button>
                  {docMenuFor === doc.id && (
                    <div className="doc-menu">
                      <button
                        className="doc-menu__item"
                        onClick={(e) => {
                          e.stopPropagation();
                          void saveDocument(doc);
                        }}
                      >
                        Скачать файл
                      </button>
                      <button
                        className="doc-menu__item"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDocMenuFor(null);
                          openCompare(doc.id);
                        }}
                      >
                        Сравнить
                      </button>
                      <button
                        className="doc-menu__item doc-menu__item--danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          setDocMenuFor(null);
                          void removeDocument(doc);
                        }}
                      >
                        Удалить файл
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </aside>

      <div
        className={`sidebar-backdrop ${sidebarOpen ? "sidebar-backdrop--visible" : ""}`}
        onClick={() => setSidebarOpen(false)}
        aria-hidden="true"
      />

      {showProfile ? (
        <ProfilePanel user={user} onBack={() => setShowProfile(false)} onDeleted={logout} />
      ) : showAdmin ? (
        <AdminPanel onBack={() => setShowAdmin(false)} currentUserId={user.id} />
      ) : (
        <main className="chat">
          <div className="chat__header">
            <div className="chat__header-main">
              <div className="chat__header-title">
                {chats.find((c) => c.id === activeChatId)?.title ?? "Спросите о документах"}
              </div>
              <div className="chat__header-sub">
                Поиск по {documents.length} {plural(documents.length, "документу", "документам", "документам")}
              </div>
            </div>
            <button
              type="button"
              className="chat__menu-btn"
              onClick={() => setSidebarOpen(true)}
              aria-label="Открыть меню"
              title="Меню"
            >
              <span className="chat__menu-icon" aria-hidden="true">
                <i />
                <i />
                <i />
              </span>
            </button>
          </div>

          <div className="chat__scroll">
            <div className="chat__messages" ref={messagesRef} onScroll={handleMessagesScroll}>
            {messagesLoading ? (
              <div className="chat__empty">
                <div className="chat__empty-icon">💬</div>
                <div className="chat__empty-sub">Загружаем переписку…</div>
              </div>
            ) : messages.length === 0 ? (
              <div className="chat__empty">
                <div className="chat__empty-icon">💬</div>
                <div className="chat__empty-title">Задайте вопрос своими словами</div>
                <div className="chat__empty-sub">
                  Загрузите документы и задавайте вопросы. Найденные фрагменты подсвечиваются в файлах.
                </div>
              </div>
            ) : (
              messages.map((m) => (
                <MessageItem key={m.id} m={m} documents={documents} onOpenSource={openSource} />
              ))
            )}
            {loading && (
              <div className="msg msg--assistant">
                <div className="msg__bubble msg__bubble--typing">
                  <span className="typing-dots">
                    <span />
                    <span />
                    <span />
                  </span>
                </div>
              </div>
            )}
            </div>
            {showScrollBottom && (
              <button
                type="button"
                className="chat__scroll-bottom"
                onClick={scrollMessagesToBottom}
                aria-label="Вниз"
                title="Вниз"
              >
                ↓
              </button>
            )}
          </div>

          {notice && <div className="chat__banner chat__banner--notice">{notice}</div>}

          {error && (
            <div className="chat__banner chat__banner--error" onClick={() => setError(null)}>
              {error}
            </div>
          )}

          {contextDocs.length > 0 && (
            <div className="chat__context-chips" aria-label="Закреплённый контекст">
              <span className="chat__context-label">Контекст:</span>
              {contextDocs.map((id) => (
                <span className="context-chip" key={id}>
                  {documents.find((d) => d.id === id)?.original_filename ?? `Документ ${id}`}
                  <button
                    type="button"
                    className="context-chip__remove"
                    onClick={() => removeContextDoc(id)}
                    title="Убрать из контекста"
                  >
                    ✕
                  </button>
                </span>
              ))}
              <button
                type="button"
                className="btn--link chat__context-clear"
                onClick={() => setContextDocs([])}
              >
                Очистить
              </button>
            </div>
          )}

          <form
            className="chat__composer"
            onSubmit={(e) => {
              e.preventDefault();
              void sendMessage(input);
            }}
          >
            <input
              ref={composerFileRef}
              type="file"
              accept=".pdf,.txt,.docx,.md,.odt"
              multiple
              hidden
              onChange={(e) => {
                requestUpload(e.target.files);
                e.target.value = "";
              }}
            />
            <button
              type="button"
              className="chat__attach"
              onClick={() => composerFileRef.current?.click()}
              title="Добавить документ"
              aria-label="Добавить документ"
            >
              +
            </button>
            <textarea
              className="chat__composer-input"
              rows={1}
              value={input}
              onChange={(e) => {
                setInput(e.target.value);
                e.target.style.height = "auto";
                e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void sendMessage(input);
                }
              }}
              placeholder="Задайте вопрос о документах…"
              disabled={loading}
            />
            <button
              type="submit"
              className="chat__send"
              disabled={loading || !input.trim()}
              aria-label="Отправить сообщение"
            >
              {loading ? "…" : "↑"}
            </button>
          </form>
        </main>
      )}

      <FileViewer
        doc={viewer}
        highlights={viewerHighlights}
        loading={viewerLoading}
        onClose={closeViewer}
      />

      {compareDocId != null && (
        <ComparePanel
          documents={documents}
          initialId={compareDocId}
          onClose={closeCompare}
        />
      )}

      {warningPending && (
        <UploadWarning
          onConfirm={confirmUploadWarning}
          onClose={() => setWarningPending(null)}
        />
      )}
    </div>
  );
}

function extOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return "";
  return filename.slice(dot + 1).toLowerCase();
}

const MessageItem = memo(function MessageItem({
  m,
  documents,
  onOpenSource,
}: {
  m: Message;
  documents: DocumentOut[];
  onOpenSource: (s: SourceRef) => void;
}) {
  return (
    <div className={`msg msg--${m.role}`}>
      <div className={`msg__bubble ${m.error ? "msg__bubble--error" : ""}`}>
        {extractCodeBlock(m.text) ? <CopyableBlock result={extractCodeBlock(m.text)!} /> : m.text}
      </div>
      {m.role === "user" && m.contextDocumentIds && m.contextDocumentIds.length > 0 && (
        <div className="msg__context-chips">
          {m.contextDocumentIds.map((id) => (
            <span className="context-chip context-chip--readonly" key={id}>
              {documents.find((d) => d.id === id)?.original_filename ?? `Документ ${id}`}
            </span>
          ))}
        </div>
      )}
      {m.role === "assistant" && m.agentSteps && m.agentSteps.length > 0 && (
        <div className="agent-steps">
          <div className="agent-steps__title">Шаги нейросети</div>
          {m.agentSteps.map((s) => (
            <div className="agent-steps__item" key={s.step_id}>
              <span className="agent-steps__icon">
                {s.status === "running" ? "⏳" : s.status === "error" ? "✗" : "✓"}
              </span>
              {s.message}
            </div>
          ))}
        </div>
      )}
      {m.role === "assistant" &&
        (() => {
          // Live-created docs (from the stream) plus any file this
          // message produced earlier and restored from the backend
          // on reload — so the card survives F5 / reopening the chat.
          const restored = m.documentId ? documents.find((d) => d.id === m.documentId) : undefined;
          const createdDocs = [...(m.createdDocuments || [])];
          if (restored && !createdDocs.some((d) => d.document_id === restored.id)) {
            createdDocs.push({
              document_id: restored.id,
              filename: restored.original_filename,
              file_type: restored.file_type,
            });
          }
          if (createdDocs.length === 0) return null;
          return (
            <div className="created-docs">
              <div className="created-docs__title">Созданные документы</div>
              {createdDocs.map((d) => (
                <div className="created-doc" key={d.document_id}>
                  <span className="created-doc__icon">{fileIcon(d.file_type)}</span>
                  <span className="created-doc__name" title={d.filename}>
                    {d.filename}
                  </span>
                  <button
                    type="button"
                    className="created-doc__btn"
                    onClick={() => void downloadDocument(d.document_id, d.filename)}
                  >
                    Скачать
                  </button>
                </div>
              ))}
            </div>
          );
        })()}
      {m.sources && m.sources.length > 0 && (
        <div className="sources">
          <div className="sources__title">
            Источники · {m.sources.length}{" "}
            {plural(m.sources.length, "совпадение", "совпадения", "совпадений")} — нажмите, чтобы открыть
          </div>
          {m.sources.slice(0, 3).map((s, i) => (
            <button key={i} className="source" onClick={() => onOpenSource(s)}>
              <div className="source__head">
                <span className="source__title">
                  <span className="source__title-icon">{fileIcon(extOf(s.filename))}</span>
                  <span className="source__title-text">{s.filename}</span>
                </span>
                <span className="source__score">{(s.score * 100).toFixed(0)}%</span>
              </div>
              <div className="source__meta">Фрагмент {s.chunk_index}</div>
              <div className="source__chunk">{s.text}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
});
