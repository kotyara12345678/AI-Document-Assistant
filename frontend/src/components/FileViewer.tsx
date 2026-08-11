import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import type { PDFDocumentProxy } from "pdfjs-dist";
import type { DocumentContent } from "../types";
import { documentFileSource } from "../api";
import { highlightSegments, type HighlightSegment } from "../highlight";
import { renderMarkdown } from "../markdown";
import pdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";

// The worker runs PDF parsing off the UI thread. Bundling it as a plain URL
// (instead of the raw module) is what makes PDF.js work inside Vite.
// The `?v=` query is a cache-buster: when the worker was briefly served as
// application/octet-stream, browsers cached that broken response under the
// hashed /assets URL with `Cache-Control: immutable`, so re-fetching the same
// URL never reaches nginx. A new query string forces a fresh fetch (nginx
// ignores it) and lets the corrected application/javascript response through.
const PDF_WORKER_URL = `${pdfWorker}?v=2`;

pdfjs.GlobalWorkerOptions.workerSrc = PDF_WORKER_URL;

interface FileViewerProps {
  doc: DocumentContent | null;
  highlights: string[];
  loading: boolean;
  onClose: () => void;
}

/**
 * Compact the extracted text for display: collapse runs of horizontal
 * whitespace to a single space, drop spaces around line breaks, and cap runs
 * of consecutive blank lines at one. Keeps single newlines (paragraphs) so the
 * text stays readable instead of showing the wide gaps left by extraction.
 */
export function normalizeContent(raw: string): string {
  return raw
    .replace(/\r\n?/g, "\n")
    .replace(/[^\S\n]+/g, " ")
    .replace(/ +\n/g, "\n")
    .replace(/\n +/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

const MIN_SCALE = 0.5;
const MAX_SCALE = 3.0;

// Preview panel resize bounds: never collapse to zero width, and never eat the
// whole chat area (sidebar is 320px, we keep at least MIN_CHAT_WIDTH for it).
const VIEWER_MIN_WIDTH = 300;
const VIEWER_MAX_WIDTH = 720;
const SIDEBAR_WIDTH = 320;
const MIN_CHAT_WIDTH = 300;

function clampScale(s: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, s));
}

function normalizeText(s: string): string {
  return s.toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * Find the PDF page whose own text contains the answer snippet. Works on the
 * original PDF via pdf.js — no backend or RAG changes, so it always matches
 * what a real source chunk quoted.
 */
async function firstPageWithText(pdf: PDFDocumentProxy, snippet: string): Promise<number> {
  const needle = normalizeText(snippet);
  const candidates = [needle, needle.slice(0, 140), needle.slice(0, 60)].filter(Boolean);
  for (let p = 1; p <= pdf.numPages; p += 1) {
    try {
      const page = await pdf.getPage(p);
      const textContent = await page.getTextContent();
      const text = normalizeText(
        textContent.items.map((item) => ("str" in item ? item.str : "")).join(" ")
      );
      if (text.length > 0 && candidates.some((c) => text.includes(c))) {
        return p;
      }
    } catch {
      /* skip unreadable page and keep searching */
    }
  }
  return 1;
}

function PdfViewer({ docId, snippets }: { docId: number; snippets: string[] }) {
  const [numPages, setNumPages] = useState(0);
  const [scale, setScale] = useState(1);
  const [currentPage, setCurrentPage] = useState(1);
  const [jump, setJump] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pageDims, setPageDims] = useState<{ width: number; height: number } | null>(null);
  const [fitMode, setFitMode] = useState<"width" | "page" | null>(null);
  const pdfRef = useRef<PDFDocumentProxy | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const pageEls = useRef<(HTMLDivElement | null)[]>([]);
  const snippetSearched = useRef(false);
  const fitModeRef = useRef<"width" | "page" | null>(null);
  const didInitFit = useRef(false);

  const file = useMemo(() => documentFileSource(docId), [docId]);

  const onLoadSuccess = useCallback((pdf: PDFDocumentProxy) => {
    pdfRef.current = pdf;
    setNumPages(pdf.numPages);
    setCurrentPage(1);
    setJump("1");
    void pdf
      .getPage(1)
      .then((page) => {
        const vp = page.getViewport({ scale: 1 });
        setPageDims({ width: vp.width, height: vp.height });
      })
      .catch(() => undefined);
  }, []);

  const applyFit = useCallback(
    (mode: "width" | "page") => {
      const el = scrollRef.current;
      if (!el || !pageDims) return;
      // The scroll area has 12px horizontal and 18px vertical padding.
      const availW = el.clientWidth - 24;
      const scaleW = availW / pageDims.width;
      if (mode === "width") {
        setScale(clampScale(scaleW));
      } else {
        const availH = el.clientHeight - 36;
        setScale(clampScale(Math.min(scaleW, availH / pageDims.height)));
      }
    },
    [pageDims]
  );

  const setFit = useCallback(
    (mode: "width" | "page") => {
      fitModeRef.current = mode;
      setFitMode(mode);
      applyFit(mode);
    },
    [applyFit]
  );

  const zoomBy = useCallback((delta: number) => {
    fitModeRef.current = null;
    setFitMode(null);
    setScale((s) => clampScale(Math.round((s + delta) * 100) / 100));
  }, []);

  // Start in fit-to-width once the first page's dimensions are known.
  useEffect(() => {
    if (!pageDims || didInitFit.current) return;
    didInitFit.current = true;
    setFit("width");
  }, [pageDims, setFit]);

  // When the panel (or window) is resized, re-apply the active fit mode so the
  // page keeps filling the container instead of staying stale.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => {
      const mode = fitModeRef.current;
      if (mode) applyFit(mode);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [applyFit]);

  const goToPage = useCallback(
    (target: number) => {
      if (numPages === 0) return;
      const page = Math.min(Math.max(1, Number.isFinite(target) ? Math.trunc(target) : 1), numPages);
      const el = pageEls.current[page - 1];
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
      setCurrentPage(page);
      setJump(String(page));
    },
    [numPages]
  );

  const onScroll = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const mid = el.scrollTop + Math.min(el.clientHeight * 0.4, 200);
    let active = 1;
    const base = el.getBoundingClientRect().top;
    pageEls.current.forEach((node, i) => {
      if (!node) return;
      const top = node.getBoundingClientRect().top - base + el.scrollTop;
      if (top <= mid) active = i + 1;
    });
    setCurrentPage(active);
  }, []);

  // When a source (snippet) is clicked, jump to the page that contains it.
  useEffect(() => {
    if (numPages === 0 || snippetSearched.current) return;
    const snippet = snippets.find((s) => s && s.trim());
    const pdf = pdfRef.current;
    if (!snippet || !pdf) return;
    snippetSearched.current = true;
    let cancelled = false;
    firstPageWithText(pdf, snippet)
      .then((page) => {
        if (!cancelled) goToPage(page);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [numPages, snippets, goToPage]);

  const submitJump = useCallback(() => {
    goToPage(parseInt(jump, 10));
  }, [goToPage, jump]);

  return (
    <div className="viewer__pdf-body">
      <div className="viewer__pdf-toolbar">
        <div className="viewer__pdf-controls">
          <button
            className="viewer__btn viewer__btn--zoom"
            onClick={() => zoomBy(-0.25)}
            title="Уменьшить масштаб"
          >
            −
          </button>
          <span className="viewer__page-info">{Math.round(scale * 100)}%</span>
          <button
            className="viewer__btn viewer__btn--zoom"
            onClick={() => zoomBy(0.25)}
            title="Увеличить масштаб"
          >
            +
          </button>
        </div>
        <div className="viewer__pdf-controls">
          <button
            className={`viewer__btn${fitMode === "width" ? " viewer__btn--active" : ""}`}
            onClick={() => setFit("width")}
            disabled={!pageDims}
            title="По ширине"
          >
            По ширине
          </button>
          <button
            className={`viewer__btn${fitMode === "page" ? " viewer__btn--active" : ""}`}
            onClick={() => setFit("page")}
            disabled={!pageDims}
            title="Вся страница"
          >
            Вся страница
          </button>
        </div>
        <div className="viewer__pdf-controls">
          <span className="viewer__page-info">Страница</span>
          <input
            className="viewer__page-input"
            type="number"
            min={1}
            max={numPages || 1}
            value={jump}
            onChange={(e) => setJump(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") submitJump();
            }}
          />
          <span className="viewer__page-info">из {numPages || "…"}</span>
          <button className="viewer__btn" onClick={submitJump} disabled={numPages === 0}>
            Перейти
          </button>
        </div>
      </div>

      <div className="viewer__pdf" ref={scrollRef} onScroll={onScroll}>
        {error ? (
          <div className="viewer__error">{error}</div>
        ) : (
          <Document
            file={file}
            onLoadSuccess={onLoadSuccess}
            onLoadError={(err) => setError(err instanceof Error ? err.message : "Не удалось загрузить PDF")}
            loading={<div className="viewer__loading">Загружаем PDF…</div>}
            error={<div className="viewer__error">Не удалось открыть PDF.</div>}
          >
            {numPages > 0 &&
              Array.from({ length: numPages }, (_, i) => i + 1).map((p) => (
                <div
                  key={p}
                  className={`viewer__pdf-page${p === currentPage ? " viewer__pdf-page--scrolled" : ""}`}
                  ref={(el) => {
                    pageEls.current[p - 1] = el;
                  }}
                >
                  <Page
                    pageNumber={p}
                    scale={scale}
                    renderTextLayer={false}
                    renderAnnotationLayer={false}
                  />
                  <span className="viewer__pdf-page-num">{p}</span>
                </div>
              ))}
          </Document>
        )}
      </div>
    </div>
  );
}

function normalizeLineEndings(s: string): string {
  return s.replace(/\r\n?/g, "\n");
}

interface RichSeg {
  text: string;
  hit: boolean;
}

type DocBlock =
  | { type: "heading"; segs: RichSeg[] }
  | { type: "paragraph"; segs: RichSeg[] }
  | { type: "ul"; items: RichSeg[][] }
  | { type: "ol"; items: RichSeg[][] };

/** Split highlight segments into physical lines (one entry per `\n` segment). */
function splitLines(segments: HighlightSegment[]): RichSeg[][] {
  const lines: RichSeg[][] = [[]];
  for (const seg of segments) {
    const parts = seg.text.split("\n");
    for (let i = 0; i < parts.length; i += 1) {
      if (i > 0) lines.push([]);
      lines[lines.length - 1].push({ text: parts[i], hit: seg.hit });
    }
  }
  return lines;
}

const ENDS_SENTENCE_RE = /[.!?:;…]$/;

function isListLine(text: string): "ul" | "ol" | null {
  const match = text.match(/^\s*(?:([-*•·◦▪])|(\d{1,3})[.)])\s+/);
  if (!match) return null;
  return match[1] ? "ul" : "ol";
}

/**
 * Conservative heading heuristic: a short line, no ending punctuation, not a
 * list item / table row, followed by more content. The backend stores DOCX
 * without style info, so this is the only signal available without changes.
 */
function isHeadingLine(text: string, hasFollowing: boolean): boolean {
  const t = text.trim();
  if (!t || t.includes("\t") || !hasFollowing) return false;
  if (t.length > 80) return false;
  if (ENDS_SENTENCE_RE.test(t)) return false;
  if (isListLine(t)) return false;
  return t.split(/\s+/).length <= 8;
}

/** Drop the leading list marker so native list bullets/numbers can render. */
function stripListItemMarker(segs: RichSeg[]): RichSeg[] {
  const text = segs.map((s) => s.text).join("");
  const match = text.match(/^\s*(?:[-*•·◦▪]|\d{1,3}[.)])\s+/);
  if (!match) return segs;
  let remaining = match[0].length;
  const out: RichSeg[] = [];
  for (const seg of segs) {
    if (remaining <= 0) {
      out.push(seg);
    } else if (seg.text.length <= remaining) {
      remaining -= seg.text.length;
    } else {
      out.push({ text: seg.text.slice(remaining), hit: seg.hit });
      remaining = 0;
    }
  }
  return out;
}

function buildDocxBlocks(lines: RichSeg[][]): DocBlock[] {
  const textOf = (segs: RichSeg[]) => segs.map((s) => s.text).join("");
  const blocks: DocBlock[] = [];
  let i = 0;
  while (i < lines.length) {
    const text = textOf(lines[i]);
    if (!text.trim()) {
      i += 1;
      continue;
    }
    const listType = isListLine(text);
    if (listType) {
      const items: RichSeg[][] = [stripListItemMarker(lines[i])];
      i += 1;
      while (i < lines.length) {
        const nextText = textOf(lines[i]);
        if (isListLine(nextText) === listType && nextText.trim()) {
          items.push(stripListItemMarker(lines[i]));
          i += 1;
        } else {
          break;
        }
      }
      blocks.push(listType === "ul" ? { type: "ul", items } : { type: "ol", items });
      continue;
    }
    let j = i + 1;
    while (j < lines.length && !textOf(lines[j]).trim()) j += 1;
    const hasFollowing = j < lines.length;
    blocks.push(
      isHeadingLine(text, hasFollowing)
        ? { type: "heading", segs: lines[i] }
        : { type: "paragraph", segs: lines[i] }
    );
    i += 1;
  }
  return blocks;
}

function Segments({ segs }: { segs: RichSeg[] }) {
  return (
    <>
      {segs.map((seg, i) =>
        seg.hit ? (
          <mark key={i} className="hl">
            {seg.text}
          </mark>
        ) : (
          <span key={i}>{seg.text}</span>
        )
      )}
    </>
  );
}

/**
 * DOCX preview: render the extracted paragraphs as a readable document with
 * paragraphs, headings and lists, keeping the reading column reasonably narrow.
 */
function DocxViewer({ raw, highlights }: { raw: string; highlights: string[] }) {
  const blocks = useMemo(
    () => buildDocxBlocks(splitLines(highlightSegments(raw, highlights))),
    [raw, highlights]
  );

  return (
    <div className="viewer__doc">
      {blocks.map((block, i) => {
        if (block.type === "heading") {
          return (
            <h3 key={i} className="viewer__doc-heading">
              <Segments segs={block.segs} />
            </h3>
          );
        }
        if (block.type === "paragraph") {
          return (
            <p key={i} className="viewer__doc-paragraph">
              <Segments segs={block.segs} />
            </p>
          );
        }
        if (block.type === "ul") {
          return (
            <ul key={i} className="viewer__doc-list">
              {block.items.map((item, j) => (
                <li key={j} className="viewer__doc-list-item">
                  <Segments segs={item} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <ol key={i} className="viewer__doc-list">
            {block.items.map((item, j) => (
              <li key={j} className="viewer__doc-list-item">
                <Segments segs={item} />
              </li>
            ))}
          </ol>
        );
      })}
    </div>
  );
}

/**
 * Markdown preview: render the source as HTML. The raw markdown is kept by the
 * backend verbatim, so marked + DOMPurify turn it into sanitized HTML (no raw
 * script, no external image requests, links open in a new tab).
 */
function MarkdownViewer({ raw }: { raw: string }) {
  const html = useMemo(() => renderMarkdown(raw), [raw]);
  return <div className="viewer__md" dangerouslySetInnerHTML={{ __html: html }} />;
}

/**
 * TXT preview: monospace, preserves every line break and wraps long lines.
 */
function TxtViewer({ raw, highlights }: { raw: string; highlights: string[] }) {
  const segments = useMemo(() => highlightSegments(raw, highlights), [raw, highlights]);

  return (
    <div className="viewer__txt">
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
  );
}

export default function FileViewer({ doc, highlights, loading, onClose }: FileViewerProps) {
  const contentRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState<number | null>(null);
  const [resizing, setResizing] = useState(false);

  const maxPanelWidth = useCallback(() => {
    // Never let the panel push the chat area out of view: cap by the room that
    // remains after the fixed sidebar and a minimum chat width.
    const room = window.innerWidth - SIDEBAR_WIDTH - MIN_CHAT_WIDTH;
    return Math.max(VIEWER_MIN_WIDTH, Math.min(VIEWER_MAX_WIDTH, room));
  }, []);

  const onResizeStart = useCallback(
    (e: ReactPointerEvent<HTMLDivElement>) => {
      // On small screens the panel is a full-height overlay with a CSS width;
      // dragging there would be confusing, so keep the CSS behavior.
      if (window.innerWidth <= 960) return;
      const startX = e.clientX;
      const startW = width ?? 460;
      e.preventDefault();
      setResizing(true);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
      const onMove = (ev: PointerEvent) => {
        const next = startW - (ev.clientX - startX);
        setWidth(Math.min(Math.max(next, VIEWER_MIN_WIDTH), maxPanelWidth()));
      };
      const onUp = () => {
        setResizing(false);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [width, maxPanelWidth]
  );

  // If the window is shrunk while a custom width is set, clamp it so the chat
  // is never squeezed out.
  useEffect(() => {
    const onResize = () => {
      setWidth((w) => (w == null ? w : Math.min(w, maxPanelWidth())));
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [maxPanelWidth]);

  const rawText = useMemo(() => (doc ? normalizeLineEndings(doc.content) : ""), [doc]);

  useEffect(() => {
    if (!doc) return;
    if (highlights.length === 0) return;
    const first = contentRef.current?.querySelector("mark");
    first?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [doc, highlights]);

  if (!doc && !loading) return null;

  const isPdf = doc?.file_type === "pdf";

  return (
    <aside className="viewer" style={width != null ? { width } : undefined}>
      <div
        className={`viewer__resize${resizing ? " viewer__resize--dragging" : ""}`}
        onPointerDown={onResizeStart}
        aria-label="Изменить ширину панели просмотра"
      />
      <div className="viewer__header">
        <div className="viewer__file">
          <span className="viewer__file-icon">
            {doc?.file_type === "pdf"
              ? "📕"
              : doc?.file_type === "docx"
                ? "📘"
                : doc?.file_type === "md"
                  ? "📝"
                  : "📄"}
          </span>
          <div className="viewer__file-info">
            <div className="viewer__file-name">{doc?.original_filename ?? "Загрузка…"}</div>
            {doc && (
              <div className="viewer__file-meta">
                <span>
                  {doc.file_type.toUpperCase()} · {formatChars(doc.content_length)}
                </span>
                {highlights.length > 0 && (
                  <span className="viewer__file-meta-hit">совпадение открыто в файле</span>
                )}
              </div>
            )}
          </div>
        </div>
        <button className="viewer__close" onClick={onClose} aria-label="Закрыть просмотр">
          ✕
        </button>
      </div>

      <div
        className={`viewer__body${isPdf ? " viewer__body--pdf" : ""}`}
        ref={contentRef}
      >
        {loading ? (
          <div className="viewer__loading">Загружаем содержимое…</div>
        ) : !doc ? null : isPdf ? (
          <PdfViewer key={doc.id} docId={doc.id} snippets={highlights} />
        ) : !rawText.trim() ? (
          <div className="viewer__error">Нет извлекаемого текста.</div>
        ) : doc.file_type === "docx" || doc.file_type === "odt" ? (
          <DocxViewer raw={rawText} highlights={highlights} />
        ) : doc.file_type === "md" ? (
          <MarkdownViewer raw={rawText} />
        ) : (
          <TxtViewer raw={rawText} highlights={highlights} />
        )}
      </div>
    </aside>
  );
}

function formatChars(n: number): string {
  if (n < 1000) return `${n} симв.`;
  return `${(n / 1000).toFixed(1)} тыс. симв.`;
}