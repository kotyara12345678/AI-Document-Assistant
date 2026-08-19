import { useCallback, useEffect, useMemo, useState } from "react";
import type { CompareResponse, DocumentOut } from "../types";
import { compareDocuments, fetchDocumentVersions } from "../api";
import { useI18n } from "../i18n";
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
  const { t } = useI18n();
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
      setError(err instanceof Error ? err.message : t("compare.errorCompare"));
    } finally {
      setLoading(false);
    }
  }, [leftId, rightId, t]);

  const versionOf = (id: number): DocumentOut | undefined =>
    documents.find((d) => d.id === id);

  const leftDoc = versionOf(leftId);

  return (
    <aside className="compare">
      <div className="compare__header">
        <div className="compare__file">
          <span className="compare__file-icon">{"⇄"}</span>
          <div className="compare__file-info">
            <div className="compare__file-name">{t("compare.title")}</div>
            <div className="compare__file-meta">
              {data
                ? `${data.left.original_filename} ↔ ${data.right.original_filename}`
                : t("compare.pickHint")}
            </div>
          </div>
        </div>
        <button className="compare__close" onClick={onClose} aria-label={t("compare.closeAria")}>
          ✕
        </button>
      </div>

      <div className="compare__controls">
        <label className="compare__pick">
          <span className="compare__pick-label">{t("compare.left")}</span>
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
          {loading ? t("compare.comparing") : t("compare.compareAction")}
        </button>

        <label className="compare__pick">
          <span className="compare__pick-label">{t("compare.right")}</span>
          <select
            className="compare__select"
            value={rightId}
            onChange={(e) => {
              setRightId(Number(e.target.value));
              setData(null);
            }}
          >
            {versions.length > 1 && (
              <optgroup label={t("compare.versionsGroup")}>
                {versions.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.original_filename}
                    {v.id === leftId ? t("compare.thisOne") : ""}
                  </option>
                ))}
              </optgroup>
            )}
            <optgroup label={t("compare.allDocs")}>
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
            <div className="compare__empty-title">{t("compare.emptyTitle")}</div>
            <div className="compare__empty-sub">{t("compare.emptySub")}</div>
            {versions.length > 1 && (
              <div className="compare__empty-versions">
                {t("compare.versionsFound", {
                  name: leftDoc?.original_filename ?? "",
                  count: versions.length,
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </aside>
  );
}
