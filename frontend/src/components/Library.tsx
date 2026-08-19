import { useMemo, useState } from "react";
import type { DocumentOut } from "../types";
import { useI18n } from "../i18n";

interface Props {
  documents: DocumentOut[];
  onBack: () => void;
  onOpen: (doc: DocumentOut) => void;
  onDownload: (doc: DocumentOut) => void;
  onDelete: (doc: DocumentOut) => void;
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

export default function Library({ documents, onBack, onOpen, onDownload, onDelete }: Props) {
  const { t, formatBytes, formatDate } = useI18n();
  const [query, setQuery] = useState("");

  const generated = useMemo(
    () =>
      documents.filter(
        (d) => d.chat_id != null || d.source_file_id != null || d.filename.startsWith("generated_"),
      ),
    [documents],
  );

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return generated;
    return generated.filter(
      (d) =>
        d.original_filename.toLowerCase().includes(q) ||
        d.filename.toLowerCase().includes(q) ||
        d.file_type.toLowerCase().includes(q),
    );
  }, [generated, query]);

  return (
    <main className="library">
      <div className="library__inner">
        <div className="library__header">
          <button className="library__back" onClick={onBack}>
            {t("library.back")}
          </button>
          <h1 className="library__title">{t("library.title")}</h1>
          <div className="library__spacer" />
        </div>

        <div className="library__search-row">
          <input
            className="library__search"
            type="text"
            placeholder={t("library.searchPh")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label={t("library.searchAria")}
          />
        </div>

        {generated.length === 0 ? (
          <div className="library__empty">
            <div className="library__empty-icon">📚</div>
            <div className="library__empty-title">{t("library.emptyTitle")}</div>
            <div className="library__empty-sub">{t("library.emptySub")}</div>
          </div>
        ) : filtered.length === 0 ? (
          <div className="library__empty">
            <div className="library__empty-title">{t("library.noResultsTitle")}</div>
            <div className="library__empty-sub">{t("library.noResultsSub")}</div>
          </div>
        ) : (
          <div className="library__grid">
            {filtered.map((doc) => (
              <div className="library-card" key={doc.id}>
                <div className="library-card__icon">{fileIcon(doc.file_type)}</div>
                <div className="library-card__body">
                  <div className="library-card__title" title={doc.original_filename}>
                    {doc.original_filename}
                  </div>
                  <div className="library-card__meta">
                    {doc.file_type.toUpperCase()} · {formatBytes(doc.file_size)} ·{" "}
                    {formatDate(doc.created_at, { day: "2-digit", month: "2-digit", year: "numeric" })}
                  </div>
                </div>
                <div className="library-card__actions">
                  <button
                    type="button"
                    className="library-card__btn"
                    onClick={() => onOpen(doc)}
                    title={t("library.open")}
                  >
                    {t("library.open")}
                  </button>
                  <button
                    type="button"
                    className="library-card__btn"
                    onClick={() => onDownload(doc)}
                    title={t("library.download")}
                  >
                    {t("library.download")}
                  </button>
                  <button
                    type="button"
                    className="library-card__btn library-card__btn--danger"
                    onClick={() => onDelete(doc)}
                    title={t("library.delete")}
                  >
                    {t("library.delete")}
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
