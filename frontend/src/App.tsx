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
import Library from "./components/Library";
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
const SIDEBAR_COLLAPSE_KEY = "docsearch-sidebar-collapsed";
const SIDEBAR_WIDTH_KEY = "docsearch-sidebar-width";
const DEFAULT_SIDEBAR_WIDTH = 300;
const MIN_SIDEBAR_WIDTH = 200;
const MAX_SIDEBAR_WIDTH = 460;
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
  const [showProfile, setShowProfile] = useState(false);
  const [showLibrary, setShowLibrary] = useState(false);
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
  // Compare mode: which document is preselected on the left (null = closed).
  const [compareDocId, setCompareDocId] = useState<number | null>(null);
  // Docs list "⋯" menu: which document's actions menu is open (null = closed).
  const [docMenuFor, setDocMenuFor] = useState<number | null>(null);
  // Chats list "⋯" menu: which chat's actions menu is open (null = closed).
  const [chatMenuFor, setChatMenuFor] = useState<number | null>(null);
  // Mobile-only drawer (left panel becomes a tab opened via the top-right button).
  const [sidebarOpen, setSidebarOpen] = useState(false);
  // Desktop: user can collapse the whole left panel. Persisted per browser.
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SIDEBAR_COLLAPSE_KEY) === "1";
    } catch {
      return false;
    }
  });
  // Desktop: user can drag-resize the panel width. Persisted per browser.
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => {
    try {
      const raw = Number(localStorage.getItem(SIDEBAR_WIDTH_KEY));
      if (Number.isFinite(raw) && raw >= MIN_SIDEBAR_WIDTH && raw <= MAX_SIDEBAR_WIDTH) return raw;
    } catch {
      /* storage unavailable */
    }
    return DEFAULT_SIDEBAR_WIDTH;
  });
  // Collapsed-rail "recent chats" popover.
  const [railChatsOpen, setRailChatsOpen] = useState(false);
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

  const toggleSidebar = useCallback(() => {
    // On desktops the left panel is a permanent column: toggle it. On mobile
    // it is a drawer opened through the same button.
    const isDesktop = window.matchMedia("(min-width: 901px)").matches;
    if (isDesktop) {
      setSidebarCollapsed((collapsed) => {
        const next = !collapsed;
        try {
          localStorage.setItem(SIDEBAR_COLLAPSE_KEY, next ? "1" : "0");
        } catch {
          /* storage unavailable */
        }
        return next;
      });
    } else {
      setSidebarOpen(true);
    }
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

  // --- Desktop drag-resize of the sidebar width (via the right-edge handle) ---
  const resizeStartRef = useRef<{ x: number; w: number } | null>(null);

  const startSidebarResize = useCallback((e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    resizeStartRef.current = { x: e.clientX, w: sidebarWidth };
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      /* not critical */
    }
    document.body.style.userSelect = "none";
  }, [sidebarWidth]);

  const moveSidebarResize = useCallback(
    (e: React.PointerEvent<HTMLElement>) => {
      const start = resizeStartRef.current;
      if (!start) return;
      const next = Math.min(MAX_SIDEBAR_WIDTH, Math.max(MIN_SIDEBAR_WIDTH, start.w + e.clientX - start.x));
      setSidebarWidth(next);
      try {
        localStorage.setItem(SIDEBAR_WIDTH_KEY, String(next));
      } catch {
        /* storage unavailable */
      }
    },
    []
  );

  const endSidebarResize = useCallback((e: React.PointerEvent<HTMLElement>) => {
    resizeStartRef.current = null;
    document.body.style.userSelect = "";
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      /* not critical */
    }
  }, []);

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

  const openDocumentById = useCallback(
    (doc: DocumentOut) => {
      void openDocument(doc.id);
    },
    [openDocument]
  );

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
    <div className={`layout ${sidebarCollapsed ? "layout--no-sidebar" : ""}`}>
      <nav className="sidebar-rail" aria-label="Быстрый доступ">
        <button
          type="button"
          className="sidebar-rail__toggle"
          onClick={toggleSidebar}
          aria-label="Показать панель"
          title="Показать панель"
        >
          <svg className="collapse-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path d="M9 22H15C20 22 22 20 22 15V9C22 4 20 2 15 2H9C4 2 2 4 2 9V15C2 20 4 22 9 22Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
            <path d="M9 2V22" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </button>

        <div className="sidebar-rail__buttons">
          <button
            type="button"
            className="sidebar-rail__btn"
            onClick={() => void newChat()}
            title="Создать чат"
            aria-label="Создать чат"
          >
            <svg className="rail-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M11 2H9C4 2 2 4 2 9V15C2 20 4 22 9 22H15C20 22 22 20 22 15V13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M16.0399 3.01928L8.15988 10.8993C7.85988 11.1993 7.55988 11.7893 7.49988 12.2193L7.06988 15.2293C6.90988 16.3193 7.67988 17.0793 8.76988 16.9293L11.7799 16.4993C12.1999 16.4393 12.7899 16.1393 13.0999 15.8393L20.9799 7.95928C22.3399 6.59928 22.9799 5.01928 20.9799 3.01928C18.9799 1.01928 17.3999 1.65928 16.0399 3.01928Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M14.9102 4.15039C15.5802 6.54039 17.4502 8.41039 19.8502 9.09039" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <button
            type="button"
            className="sidebar-rail__btn"
            onClick={() => { setShowLibrary((v) => !v); setShowAdmin(false); setShowProfile(false); }}
            title="Библиотека"
            aria-label="Библиотека"
          >
            <svg className="rail-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M21 7V17C21 20 19.5 22 16 22H8C4.5 22 3 20 3 17V7C3 4 4.5 2 8 2H16C19.5 2 21 4 21 7Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M15.5 2V9.85999C15.5 10.3 14.98 10.52 14.66 10.23L12.34 8.09003C12.15 7.91003 11.85 7.91003 11.66 8.09003L9.34003 10.23C9.02003 10.52 8.5 10.3 8.5 9.85999V2H15.5Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M13.25 14H17.5" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M9 18H17.5" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          <div className="sidebar-rail__btn-wrap">
            <button
              type="button"
              className="sidebar-rail__btn"
              onClick={() => setRailChatsOpen((v) => !v)}
              title="Недавние чаты"
              aria-label="Недавние чаты"
            >
              <svg className="rail-icon" width="24" height="24" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <g clipPath="url(#rail-chats-clip)">
                  <path d="M17.6201 9.61914H12.3701C11.9601 9.61914 11.6201 9.27914 11.6201 8.86914C11.6201 8.45914 11.9601 8.11914 12.3701 8.11914H17.6201C18.0301 8.11914 18.3701 8.45914 18.3701 8.86914C18.3701 9.27914 18.0401 9.61914 17.6201 9.61914Z" />
                  <path d="M7.12006 10.3803C6.93006 10.3803 6.74006 10.3103 6.59006 10.1603L5.84006 9.41031C5.55006 9.12031 5.55006 8.64031 5.84006 8.35031C6.13006 8.06031 6.61006 8.06031 6.90006 8.35031L7.12006 8.57031L8.84006 6.85031C9.13006 6.56031 9.61006 6.56031 9.90006 6.85031C10.1901 7.14031 10.1901 7.62031 9.90006 7.91031L7.65006 10.1603C7.51006 10.3003 7.32006 10.3803 7.12006 10.3803Z" />
                  <path d="M17.6201 16.6191H12.3701C11.9601 16.6191 11.6201 16.2791 11.6201 15.8691C11.6201 15.4591 11.9601 15.1191 12.3701 15.1191H17.6201C18.0301 15.1191 18.3701 15.4591 18.3701 15.8691C18.3701 16.2791 18.0401 16.6191 17.6201 16.6191Z" />
                  <path d="M7.12006 17.3803C6.93006 17.3803 6.74006 17.3103 6.59006 17.1603L5.84006 16.4103C5.55006 16.1203 5.55006 15.6403 5.84006 15.3503C6.13006 15.0603 6.61006 15.0603 6.90006 15.3503L7.12006 15.5703L8.84006 13.8503C9.13006 13.5603 9.61006 13.5603 9.90006 13.8503C10.1901 14.1403 10.1901 14.6203 9.90006 14.9103L7.65006 17.1603C7.51006 17.3003 7.32006 17.3803 7.12006 17.3803Z" />
                  <path d="M15 22.75H9C3.57 22.75 1.25 20.43 1.25 15V9C1.25 3.57 3.57 1.25 9 1.25H15C20.43 1.25 22.75 3.57 22.75 9V15C22.75 20.43 20.43 22.75 15 22.75ZM9 2.75C4.39 2.75 2.75 4.39 2.75 9V15C2.75 19.61 4.39 21.25 9 21.25H15C19.61 21.25 21.25 19.61 21.25 15V9C21.25 4.39 19.61 2.75 15 2.75H9Z" />
                </g>
                <defs>
                  <clipPath id="rail-chats-clip">
                    <rect width="24" height="24" fill="none" />
                  </clipPath>
                </defs>
              </svg>
            </button>
            {railChatsOpen && (
              <>
                <div className="doc-menu-backdrop" onClick={() => setRailChatsOpen(false)} />
                <div className="rail-chats">
                  <div className="rail-chats__title">Недавние чаты</div>
                  {chatsLoading ? (
                    <div className="rail-chats__empty">Загружаем…</div>
                  ) : chats.length === 0 ? (
                    <div className="rail-chats__empty">Чатов пока нет.</div>
                  ) : (
                    [...chats].slice(0, 10).map((chat) => (
                      <button
                        key={chat.id}
                        className={`rail-chats__item ${activeChatId === chat.id ? "rail-chats__item--active" : ""}`}
                        onClick={() => {
                          void selectChat(chat.id);
                          setRailChatsOpen(false);
                        }}
                      >
                        <span className="rail-chats__item-title">{chat.title}</span>
                      </button>
                    ))
                  )}
                </div>
              </>
            )}
          </div>
        </div>

        <button
          className="sidebar-rail__avatar"
          onClick={() => {
            setShowProfile(true);
            setShowAdmin(false);
            setShowLibrary(false);
          }}
          title="Личный кабинет"
        >
          {user.avatar_url ? (
            <img className="sidebar-rail__avatar-img" src={user.avatar_url} alt="Фото профиля" />
          ) : (
            <span className="sidebar-rail__avatar-letter">{user.email.slice(0, 1).toUpperCase()}</span>
          )}
        </button>
      </nav>

      <aside
        className={`sidebar ${sidebarOpen ? "sidebar--open" : ""}`}
        style={{ width: sidebarWidth }}
        onPointerDown={onSidebarPointerDown}
        onPointerMove={onSidebarPointerMove}
        onPointerUp={finishSidebarDrag}
        onPointerCancel={clearSidebarDrag}
      >
        <div
          className="sidebar__resizer"
          onPointerDown={startSidebarResize}
          onPointerMove={moveSidebarResize}
          onPointerUp={endSidebarResize}
          onPointerCancel={endSidebarResize}
        />
        <div className="sidebar__header">
          <span className="sidebar__brand">ADA</span>
          <div className="sidebar__actions">
            <button
              type="button"
              className="sidebar__collapse-btn"
              onClick={toggleSidebar}
              aria-label="Скрыть панель"
              title="Скрыть панель"
            >
              <svg className="collapse-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path d="M9 22H15C20 22 22 20 22 15V9C22 4 20 2 15 2H9C4 2 2 4 2 9V15C2 20 4 22 9 22Z" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M9 2V22" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
            <button
              className="sidebar__close-btn"
              onClick={() => setSidebarOpen(false)}
              title="Закрыть меню"
              aria-label="Закрыть меню"
            >
              ✕
            </button>
          </div>
        </div>

<button className="sidebar__user" onClick={() => { setShowProfile(true); setShowAdmin(false); setShowLibrary(false); setSidebarOpen(false); }} title="Личный кабинет">
          {user.avatar_url ? (
            <img className="sidebar__user-avatar" src={user.avatar_url} alt="Фото профиля" />
          ) : (
            <span className="sidebar__user-avatar">{user.email.slice(0, 1).toUpperCase()}</span>
          )}
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
<button className="btn--admin" onClick={() => { setSidebarOpen(false); setShowAdmin((v) => !v); setShowProfile(false); setShowLibrary(false); }}>
              {showAdmin ? "◀ К чату" : "⚙ Админ-панель"}
            </button>
          </div>
        )}

        <div className="sidebar__section sidebar__section--scroll">
          <div className="sidebar__section-title">Чаты</div>
          <button className="btn--new-chat" onClick={() => void newChat()}>
            <svg className="new-chat-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M11 2H9C4 2 2 4 2 9V15C2 20 4 22 9 22H15C20 22 22 20 22 15V13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M16.0399 3.01928L8.15988 10.8993C7.85988 11.1993 7.55988 11.7893 7.49988 12.2193L7.06988 15.2293C6.90988 16.3193 7.67988 17.0793 8.76988 16.9293L11.7799 16.4993C12.1999 16.4393 12.7899 16.1393 13.0999 15.8393L20.9799 7.95928C22.3399 6.59928 22.9799 5.01928 20.9799 3.01928C18.9799 1.01928 17.3999 1.65928 16.0399 3.01928Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M14.9102 4.15039C15.5802 6.54039 17.4502 8.41039 19.8502 9.09039" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Новый чат
          </button>
          <button
            type="button"
            className="btn--new-chat"
            onClick={() => { setSidebarOpen(false); setShowLibrary((v) => !v); setShowAdmin(false); setShowProfile(false); }}
            title="Библиотека сгенерированных файлов"
          >
            <svg className="new-chat-icon" width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">
              <path d="M21 7V17C21 20 19.5 22 16 22H8C4.5 22 3 20 3 17V7C3 4 4.5 2 8 2H16C19.5 2 21 4 21 7Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M15.5 2V9.85999C15.5 10.3 14.98 10.52 14.66 10.23L12.34 8.09003C12.15 7.91003 11.85 7.91003 11.66 8.09003L9.34003 10.23C9.02003 10.52 8.5 10.3 8.5 9.85999V2H15.5Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M13.25 14H17.5" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M9 18H17.5" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Библиотека
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
                        <svg className="doc-menu__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                          <path d="M13.26 3.59924L5.04997 12.2892C4.73997 12.6192 4.43997 13.2692 4.37997 13.7192L4.00997 16.9592C3.87997 18.1292 4.71997 18.9292 5.87997 18.7292L9.09997 18.1792C9.54997 18.0992 10.18 17.7692 10.49 17.4292L18.7 8.73924C20.12 7.23924 20.76 5.52924 18.55 3.43924C16.35 1.36924 14.68 2.09924 13.26 3.59924Z" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
                          <path d="M11.89 5.05078C12.32 7.81078 14.56 9.92078 17.34 10.2008" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
                          <path d="M3 22H21" stroke="currentColor" strokeWidth="1.5" strokeMiterlimit="10" strokeLinecap="round" strokeLinejoin="round" />
                        </svg>
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
                        <svg className="doc-menu__icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                          <path d="M21.0699 5.23C19.4599 5.07 17.8499 4.95 16.2299 4.86V4.85L16.0099 3.55C15.8599 2.63 15.6399 1.25 13.2999 1.25H10.6799C8.34991 1.25 8.12991 2.57 7.96991 3.54L7.75991 4.82C6.82991 4.88 5.89991 4.94 4.96991 5.03L2.92991 5.23C2.50991 5.27 2.20991 5.64 2.24991 6.05C2.28991 6.46 2.64991 6.76 3.06991 6.72L5.10991 6.52C10.3499 6 15.6299 6.2 20.9299 6.73C20.9599 6.73 20.9799 6.73 21.0099 6.73C21.3899 6.73 21.7199 6.44 21.7599 6.05C21.7899 5.64 21.4899 5.27 21.0699 5.23Z" />
                          <path d="M19.23 8.14C18.99 7.89 18.66 7.75 18.32 7.75H5.67999C5.33999 7.75 4.99999 7.89 4.76999 8.14C4.53999 8.39 4.40999 8.73 4.42999 9.08L5.04999 19.34C5.15999 20.86 5.29999 22.76 8.78999 22.76H15.21C18.7 22.76 18.84 20.87 18.95 19.34L19.57 9.09C19.59 8.73 19.46 8.39 19.23 8.14ZM13.66 17.75H10.33C9.91999 17.75 9.57999 17.41 9.57999 17C9.57999 16.59 9.91999 16.25 10.33 16.25H13.66C14.07 16.25 14.41 16.59 14.41 17C14.41 17.41 14.07 17.75 13.66 17.75ZM14.5 13.75H9.49999C9.08999 13.75 8.74999 13.41 8.74999 13C8.74999 12.59 9.08999 12.25 9.49999 12.25H14.5C14.91 12.25 15.25 12.59 15.25 13C15.25 13.41 14.91 13.75 14.5 13.75Z" />
                        </svg>
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
                        <svg className="doc-menu__icon" width="16" height="16" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                          <path d="M21.0699 5.23C19.4599 5.07 17.8499 4.95 16.2299 4.86V4.85L16.0099 3.55C15.8599 2.63 15.6399 1.25 13.2999 1.25H10.6799C8.34991 1.25 8.12991 2.57 7.96991 3.54L7.75991 4.82C6.82991 4.88 5.89991 4.94 4.96991 5.03L2.92991 5.23C2.50991 5.27 2.20991 5.64 2.24991 6.05C2.28991 6.46 2.64991 6.76 3.06991 6.72L5.10991 6.52C10.3499 6 15.6299 6.2 20.9299 6.73C20.9599 6.73 20.9799 6.73 21.0099 6.73C21.3899 6.73 21.7199 6.44 21.7599 6.05C21.7899 5.64 21.4899 5.27 21.0699 5.23Z" />
                          <path d="M19.23 8.14C18.99 7.89 18.66 7.75 18.32 7.75H5.67999C5.33999 7.75 4.99999 7.89 4.76999 8.14C4.53999 8.39 4.40999 8.73 4.42999 9.08L5.04999 19.34C5.15999 20.86 5.29999 22.76 8.78999 22.76H15.21C18.7 22.76 18.84 20.87 18.95 19.34L19.57 9.09C19.59 8.73 19.46 8.39 19.23 8.14ZM13.66 17.75H10.33C9.91999 17.75 9.57999 17.41 9.57999 17C9.57999 16.59 9.91999 16.25 10.33 16.25H13.66C14.07 16.25 14.41 16.59 14.41 17C14.41 17.41 14.07 17.75 13.66 17.75ZM14.5 13.75H9.49999C9.08999 13.75 8.74999 13.41 8.74999 13C8.74999 12.59 9.08999 12.25 9.49999 12.25H14.5C14.91 12.25 15.25 12.59 15.25 13C15.25 13.41 14.91 13.75 14.5 13.75Z" />
                        </svg>
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
        <ProfilePanel
          user={user}
          onBack={() => setShowProfile(false)}
          onUserUpdated={setUser}
          theme={theme}
          onToggleTheme={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          onLogout={logout}
          onDeleted={logout}
        />
      ) : showLibrary ? (
        <Library
          documents={documents}
          onBack={() => setShowLibrary(false)}
          onOpen={openDocumentById}
          onDownload={saveDocument}
          onDelete={removeDocument}
        />
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
              onClick={() => toggleSidebar()}
              aria-label={sidebarCollapsed ? "Показать боковую панель" : "Скрыть боковую панель"}
              title={sidebarCollapsed ? "Показать боковую панель" : "Скрыть боковую панель"}
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
              {loading ? (
                "…"
              ) : (
                <svg className="chat__send-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
                  <path d="M12 19V5" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                  <path d="M6 11L12 5L18 11" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
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
