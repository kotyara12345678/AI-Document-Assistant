import { useEffect, useMemo, useRef } from "react";
import type { DocumentContent } from "../types";
import { highlightSegments } from "../highlight";

interface FileViewerProps {
  doc: DocumentContent | null;
  highlights: string[];
  loading: boolean;
  onClose: () => void;
}

export default function FileViewer({ doc, highlights, loading, onClose }: FileViewerProps) {
  const contentRef = useRef<HTMLDivElement>(null);

  const segments = useMemo(
    () => (doc ? highlightSegments(doc.content, highlights) : []),
    [doc, highlights]
  );

  useEffect(() => {
    if (!doc) return;
    if (highlights.length === 0) return;
    const first = contentRef.current?.querySelector("mark");
    first?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [doc, highlights]);

  if (!doc && !loading) return null;

  return (
    <aside className="viewer">
      <div className="viewer__header">
        <div className="viewer__title">
          <span className="viewer__file-icon">
            {doc?.file_type === "pdf" ? "📕" : doc?.file_type === "docx" ? "📘" : "📄"}
          </span>
          <div className="viewer__file-info">
            <div className="viewer__file-name">{doc?.original_filename ?? "Loading…"}</div>
            {doc && (
              <div className="viewer__file-meta">
                {doc.file_type.toUpperCase()} · {formatChars(doc.content_length)}
                {highlights.length > 0 && <span className="viewer__file-meta-hit"> · matches highlighted</span>}
              </div>
            )}
          </div>
        </div>
        <button className="viewer__close" onClick={onClose} aria-label="Close viewer">
          ✕
        </button>
      </div>

      <div className="viewer__body" ref={contentRef}>
        {loading ? (
          <div className="viewer__empty">Loading content…</div>
        ) : segments.length === 0 ? (
          <div className="viewer__empty">No extractable text.</div>
        ) : (
          <div className="viewer__content">
            {segments.map((seg, i) =>
              seg.hit ? (
                <mark key={i} className="hl">
                  {seg.text}
                </mark>
              ) : (
                <span key={i}>{seg.text}</span>
              )
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function formatChars(n: number): string {
  if (n < 1000) return `${n} chars`;
  return `${(n / 1000).toFixed(1)}k chars`;
}
