import { useCallback, useEffect, useMemo, useState } from "react";
import type { CompareResponse, DocumentOut } from "../types";
import { compareDocuments, fetchDocumentVersions } from "../api";
import CompareViewer from "./CompareViewer";

interface ComparePanelProps {
  documents: DocumentOut[];
  /** document preselected as the left side when the panel opens. */
  initialId: number | null;
  onClose: () => void;
}

/**
 * Compare mode: pick any two documents (or any two versions of one document)
 * and render a side-by-side diff. The version chain of the left document is
 * loaded and offered in the right-hand picker so "compare versions" is just
 * picking the original / an earlier edit.
 */
export default function ComparePanel({ documents, initialId, onClose }: ComparePanelProps) {
  const sorted = useMemo(
    () => [...documents].sort((a, b) => a.original_filename.localeCompare(b.original_filename)),
    [documents],
  );

  const [leftId, setLeftId] = useState<number>(initialId ?? sorted[0]?.id ?? 0);
  const [rightId, setRightId] = useState<number>(() => {
    const first = initialId ?? sorted[0]?.id;
    const second = sorted.find((d) => d.id !== first);
    return second?.id ?? first ?? 0;
  });
  const [versions, setVersions] = useState<DocumentOut[]>([]);
  const [data, setData] = useState<CompareResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load the version chain of the left document so "compare with original"
  // is one click (the version list also confirms whether it has versions).
  useEffect(() => {
    if (!leftId) return;
    let cancelled = false;
    setVersions([]);
    fetchDocumentVersions(leftId)
      .then((list) => {
        if (!cancelled) setVersions(list);
      })
      .catch(() => {
        /* versions are an enhancement; the picker still lists all documents */
      });
    return () => {
      cancelled = true;
    };
  }, [leftId]);

  const run = useCallback(async () => {
    if (!leftId || !rightId || leftId === rightId) return;
    setLoading(true);
    setError(null);
    try {
      setData(await compareDocuments(leftId, rightId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Не удалось сравнить документы");
    } finally {
      setLoading(false);
    }
  }, [leftId, rightId]);

  const versionOf = (id: number): DocumentOut | undefined =>
    documents.find((d) => d.id === id);

  const leftDoc = versionOf(leftId);

  return (
    <aside className="compare">
      <div className="compare__header">
        <div className="compare__file">
          <span className="compare__file-icon">{"⇄"}</span>
          <div className="compare__file-info">
            <div className="compare__file-name">Сравнение документов</div>
            <div className="compare__file-meta">
              {data
                ? `${data.left.original_filename} ↔ ${data.right.original_filename}`
                : "Выберите два файла или две версии"}
            </div>
          </div>
        </div>
        <button className="compare__close" onClick={onClose} aria-label="Закрыть сравнение">
          ✕
        </button>
      </div>

      <div className="compare__controls">
        <label className="compare__pick">
          <span className="compare__pick-label">Слева</span>
          <select
            className="compare__select"
            value={leftId}
            onChange={(e) => {
              setLeftId(Number(e.target.value));
              setData(null);
            }}
          >
            {sorted.map((d) => (
              <option key={d.id} value={d.id}>
                {d.original_filename}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          className="compare__run"
          disabled={!leftId || !rightId || leftId === rightId || loading}
          onClick={() => void run()}
        >
          {loading ? "Сравниваем…" : "Сравнить"}
        </button>

        <label className="compare__pick">
          <span className="compare__pick-label">Справа</span>
          <select
            className="compare__select"
            value={rightId}
            onChange={(e) => {
              setRightId(Number(e.target.value));
              setData(null);
            }}
          >
            {versions.length > 1 && (
              <optgroup label="Версии этого документа">
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.original_filename}
                    {v.id === leftId ? " (этот)" : ""}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label="Все документы">
              {sorted.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.original_filename}
                </option>
              ))}
            </optgroup>
          </select>
        </label>
      </div>

      {error && (
        <div className="compare__error" onClick={() => setError(null)}>
          {error}
        </div>
      )}

      {data ? (
        <CompareViewer data={data} />
      ) : (
        <div className="compare__body">
          <div className="compare__empty">
            <div className="compare__empty-title">Что вы хотите сравнить?</div>
            <div className="compare__empty-sub">
              Выберите два документа слева и справа и нажмите «Сравнить». Если файл был
              создан редактированием другого, его версии появятся в списке справа.
            </div>
            {versions.length > 1 && (
              <div className="compare__empty-versions">
                У «{leftDoc?.original_filename ?? ""}» обнаружено версий: {versions.length}
              </div>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
