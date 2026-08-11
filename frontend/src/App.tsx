import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatOut, ChatResponse, DocumentContent, DocumentOut, MessageOut, SourceRef, UserOut } from "./types";
import {
  createChat,
  deleteAllDocuments,
  deleteChat,
  deleteDocument,
  fetchChatMessages,
  fetchChats,
  fetchDocumentContent,
  fetchDocuments,
  fetchMe,
  getToken,
  sendChat,
  setToken,
  uploadDocuments,
} from "./api";
import UploadDropzone from "./components/UploadDropzone";
import FileViewer from "./components/FileViewer";
import AuthScreen from "./components/AuthScreen";
import AdminPanel from "./components/AdminPanel";

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

  const noticeTimer = useRef<number | null>(null);
  const composerFileRef = useRef<HTMLInputElement | null>(null);

  const flashNotice = useCallback((msg: string) => {
    setNotice(msg);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setNotice(null), 4000);
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

  const openDocument = useCallback(async (id: number, highlights: string[] = []) => {
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

  const onUploaded = useCallback(
    (doc: DocumentOut) => {
      setDocuments((prev) => [doc, ...prev]);
      setError(null);
      void openDocument(doc.id);
      flashNotice(`Документ «${doc.original_filename}» загружен и проиндексирован.`);
    },
    [openDocument, flashNotice]
  );

  const uploadFiles = useCallback(
    async (files: FileList | null) => {
      if (!files || files.length === 0) return;
      try {
        const docs = await uploadDocuments(Array.from(files));
        docs.forEach((doc) => onUploaded(doc));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось загрузить документы");
      }
    },
    [onUploaded]
  );

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
            text: err instanceof Error ? err.message : "Что-то пошло не так",
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
    return <AuthScreen onAuthed={setUser} />;
  }

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="sidebar__header">
          <span className="sidebar__brand">ADA</span>
          <div className="sidebar__actions">
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

        <div className="sidebar__user">
          <span className="sidebar__user-avatar">{user.email.slice(0, 1).toUpperCase()}</span>
          <div className="sidebar__user-info">
            <div className="sidebar__user-name" title={user.email}>
              {user.email}
            </div>
            <div className="sidebar__user-meta">
              {user.role === "admin" ? "Администратор" : "Пользователь"}
            </div>
          </div>
        </div>

        {user.role === "admin" && (
          <div className="sidebar__section">
            <button className="btn--admin" onClick={() => setShowAdmin((v) => !v)}>
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
                <button
                  className="chat-item__delete"
                  title="Удалить чат"
                  onClick={(e) => {
                    e.stopPropagation();
                    void removeChat(chat.id);
                  }}
                >
                  ✕
                </button>
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
                className={`doc-item ${viewer?.id === doc.id ? "doc-item--active" : ""}`}
                onClick={() => void openDocument(doc.id)}
              >
                <div className="doc-item__icon">{fileIcon(doc.file_type)}</div>
                <div className="doc-item__body">
                  <div className="doc-item__title">{doc.original_filename}</div>
                  <div className="doc-item__meta">
                    {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)}
                  </div>
                </div>
                <button
                  className="doc-item__delete"
                  title="Удалить документ"
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

      {showAdmin ? (
        <AdminPanel onBack={() => setShowAdmin(false)} />
      ) : (
        <main className="chat">
          <div className="chat__header">
            <div className="chat__header-title">
              {chats.find((c) => c.id === activeChatId)?.title ?? "Спросите о документах"}
            </div>
            <div className="chat__header-sub">
              Поиск по {documents.length} {plural(documents.length, "документу", "документам", "документам")}
            </div>
          </div>

          <div className="chat__messages">
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
                <div key={m.id} className={`msg msg--${m.role}`}>
                  <div className={`msg__bubble ${m.error ? "msg__bubble--error" : ""}`}>{m.text}</div>
                  {m.sources && m.sources.length > 0 && (
                    <div className="sources">
                      <div className="sources__title">
                        Источники · {m.sources.length}{" "}
                        {plural(m.sources.length, "совпадение", "совпадения", "совпадений")} — нажмите, чтобы открыть
                      </div>
                      {m.sources.slice(0, 3).map((s, i) => (
                        <button key={i} className="source" onClick={() => openSource(s)}>
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

          {notice && <div className="chat__banner chat__banner--notice">{notice}</div>}

          {error && (
            <div className="chat__banner chat__banner--error" onClick={() => setError(null)}>
              {error}
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
                void uploadFiles(e.target.files);
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
    </div>
  );
}

function extOf(filename: string): string {
  const dot = filename.lastIndexOf(".");
  if (dot < 0) return "";
  return filename.slice(dot + 1).toLowerCase();
}
