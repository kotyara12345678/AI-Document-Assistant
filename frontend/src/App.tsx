import { useCallback, useEffect, useState } from "react";
import type { ChatResponse, DocumentContent, DocumentOut, SourceRef } from "./types";
import { fetchDocumentContent } from "./api";
import UploadDropzone from "./components/UploadDropzone";
import FileViewer from "./components/FileViewer";

interface Message {
  id: number;
  role: "user" | "assistant";
  text: string;
  sources?: SourceRef[];
  error?: boolean;
}

let msgId = 0;

export default function App() {
  const [documents, setDocuments] = useState<DocumentOut[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [docsLoading, setDocsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [viewer, setViewer] = useState<DocumentContent | null>(null);
  const [viewerHighlights, setViewerHighlights] = useState<string[]>([]);
  const [viewerLoading, setViewerLoading] = useState(false);

  const loadDocuments = useCallback(async () => {
    try {
      const res = await fetch("/api/documents");
      if (!res.ok) throw new Error("Failed to load documents");
      const data = (await res.json()) as DocumentOut[];
      setDocuments(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load documents");
    } finally {
      setDocsLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadDocuments();
  }, [loadDocuments]);

  const openDocument = useCallback(async (id: number, highlights: string[] = []) => {
    setViewerLoading(true);
    setViewerHighlights(highlights);
    try {
      const doc = await fetchDocumentContent(id);
      setViewer(doc);
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
      setMessages((prev) => [
        ...prev,
        {
          id: msgId++,
          role: "assistant",
          text: `Document "${doc.original_filename}" uploaded and indexed successfully. Ask a question — I search across all your documents.`,
        },
      ]);
    },
    [openDocument]
  );

  const sendMessage = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || loading) return;

      // Search always runs across all documents.
      const req = { question: trimmed };
      setMessages((prev) => [...prev, { id: msgId++, role: "user", text: trimmed }]);
      setInput("");
      setLoading(true);

      try {
        const res = await fetch("/api/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(req),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || `Chat request failed (${res.status})`);
        }
        const data = (await res.json()) as ChatResponse;
        setMessages((prev) => [
          ...prev,
          { id: msgId++, role: "assistant", text: data.answer, sources: data.sources },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: msgId++,
            role: "assistant",
            text: err instanceof Error ? err.message : "Something went wrong",
            error: true,
          },
        ]);
      } finally {
        setLoading(false);
      }
    },
    [loading]
  );

  const openSource = useCallback(
    (s: SourceRef) => {
      // Highlight the exact retrieved chunk inside the source document.
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
        </div>
        <UploadDropzone onUploaded={onUploaded} onError={setError} />
        <div className="sidebar__section">
          <div className="sidebar__section-title">Your documents</div>
        </div>
        <div className="sidebar__list">
          {docsLoading ? (
            <div className="empty">Loading documents…</div>
          ) : documents.length === 0 ? (
            <div className="empty">No documents yet. Upload your first file.</div>
          ) : (
            documents.map((doc) => (
              <button
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
              </button>
            ))
          )}
        </div>
      </aside>

      <main className="chat">
        <div className="chat__header">
          <div className="chat__header-title">Ask anything</div>
          <div className="chat__header-sub">Searches across all {documents.length} documents</div>
        </div>

        <div className="chat__messages">
          {messages.length === 0 ? (
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
