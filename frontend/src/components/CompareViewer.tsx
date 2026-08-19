import { useMemo } from "react";
import type { CompareResponse } from "../types";
import { diffLines, mergeSegs } from "../diff";
import type { WordDiffSeg } from "../diff";
import { useI18n } from "../i18n";

interface CompareViewerProps {
  data: CompareResponse;
}

/**
 * Side-by-side document comparison rendered from the backend line-level diff.
 *
 * Each operation range is rendered as rows aligned left/right:
 *  - "equal" rows are kept as short context (not highlighted);
 *  - "delete" shows the old lines on the left (red);
 *  - "insert" shows the new lines on the right (green);
 *  - "replace" shows both, with a word-level diff inside the changed lines.
 *
 * Row heights are aligned because every row spans both columns, so scrolling
 * stays naturally in sync without any custom scroll plumbing.
 */
export default function CompareViewer({ data }: CompareViewerProps) {
  const { t } = useI18n();
  const rows = useMemo(() => buildRows(data), [data]);

  const rowLabel = (kind: RowKind): string => {
    switch (kind) {
      case "delete":
        return t("compareViewer.rowDeleted");
      case "insert":
        return t("compareViewer.rowAdded");
      case "replace":
        return t("compareViewer.rowChanged");
      default:
        return t("compareViewer.rowContext");
    }
  };

  return (
    <>
      <div className="compare__summary">
        <span className="compare__summary-item compare__summary-item--add">
          {t("compareViewer.added", { count: data.summary.added_lines })}
        </span>
        <span className="compare__summary-item compare__summary-item--del">
          {t("compareViewer.removed", { count: data.summary.removed_lines })}
        </span>
        <span className="compare__summary-item compare__summary-item--chg">
          {t("compareViewer.changed", { count: data.summary.changed_lines })}
        </span>
        <span className="compare__summary-item compare__summary-item--same">
          {t("compareViewer.unchanged", { count: data.summary.unchanged_lines })}
        </span>
        {data.truncated && (
          <span className="compare__summary-item compare__summary-item--warn">
            {t("compareViewer.truncated", { count: data.limit })}
          </span>
        )}
      </div>

      <div className="compare__body">
        {data.equal ? (
          <div className="compare__empty">
            <div className="compare__empty-title">{t("compareViewer.equalTitle")}</div>
            <div className="compare__empty-sub">
              {t("compareViewer.equalSub", {
                a: data.left.original_filename,
                b: data.right.original_filename,
              })}
            </div>
          </div>
        ) : (
          <div className="compare__table">
            {rows.map((row, i) => (
              <div
                key={i}
                className={`compare__row compare__row--${row.kind}`}
                aria-label={rowLabel(row.kind)}
              >
                <Cell
                  lines={row.left}
                  nums={row.leftNums}
                  side="left"
                  kind={row.kind}
                  wordDiff={row.wordDiff?.left}
                />
                <Cell
                  lines={row.right}
                  nums={row.rightNums}
                  side="right"
                  kind={row.kind}
                  wordDiff={row.wordDiff?.right}
                />
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- helpers */

type RowKind = "context" | "delete" | "insert" | "replace";

interface Row {
  kind: RowKind;
  left: string[];
  leftNums: number[];
  right: string[];
  rightNums: number[];
  wordDiff?: { left: WordDiffSeg[][]; right: WordDiffSeg[][] };
}

function buildRows(data: CompareResponse): Row[] {
  const rows: Row[] = [];
  const { left_lines: ll, right_lines: rl, operations: ops } = data;

  const CONTEXT = 4; // lines of context kept around a collapsed block

  for (let i = 0; i < ops.length; i += 1) {
    const op = ops[i];
    if (op.kind !== "equal") {
      const leftLines = ll.slice(op.left_start, op.left_end);
      const rightLines = rl.slice(op.right_start, op.right_end);
      const leftNums = range(op.left_start, op.left_end);
      const rightNums = range(op.right_start, op.right_end);
      if (op.kind === "replace") {
        rows.push({
          kind: "replace",
          left: leftLines,
          leftNums,
          right: rightLines,
          rightNums,
          wordDiff: {
            left: leftLines.map((l, idx) =>
              mergeSegs(diffLines(l, rightLines[idx] ?? "").leftSegs)
            ),
            right: rightLines.map((r, idx) =>
              mergeSegs(diffLines(leftLines[idx] ?? "", r).rightSegs)
            ),
          },
        });
      } else if (op.kind === "delete") {
        rows.push({ kind: "delete", left: leftLines, leftNums, right: [], rightNums: [] });
      } else {
        rows.push({ kind: "insert", left: [], leftNums: [], right: rightLines, rightNums });
      }
      continue;
    }

    // Equal block: keep short runs, collapse long ones to a few context lines.
    const span = op.left_end - op.left_start;
    if (span <= CONTEXT * 2) {
      pushContext(rows, ll, rl, op.left_start, op.left_end, "both");
    } else {
      pushContext(rows, ll, rl, op.left_start, op.left_start + CONTEXT, "both");
      rows.push({ kind: "context", left: [], leftNums: [], right: [], rightNums: [] });
      pushContext(rows, ll, rl, op.left_end - CONTEXT, op.left_end, "both");
    }
  }

  return rows;
}

function pushContext(
  rows: Row[],
  ll: string[],
  rl: string[],
  start: number,
  end: number,
  side: "left" | "right" | "both",
) {
  if (end <= start) return;
  if (side === "both") {
    rows.push({
      kind: "context",
      left: ll.slice(start, end),
      leftNums: range(start, end),
      right: rl.slice(start, end),
      rightNums: range(start, end),
    });
  } else if (side === "left") {
    rows.push({
      kind: "context",
      left: ll.slice(start, end),
      leftNums: range(start, end),
      right: [],
      rightNums: [],
    });
  } else {
    rows.push({
      kind: "context",
      left: [],
      leftNums: [],
      right: rl.slice(start, end),
      rightNums: range(start, end),
    });
  }
}

function range(start: number, end: number): number[] {
  const out: number[] = [];
  for (let i = start; i < end; i += 1) out.push(i + 1);
  return out;
}

function Cell({
  lines,
  nums,
  side,
  kind,
  wordDiff,
}: {
  lines: string[];
  nums: number[];
  side: "left" | "right";
  kind: RowKind;
  wordDiff?: WordDiffSeg[][];
}) {
  const empty = lines.length === 0;
  return (
    <div className={`compare__cell compare__cell--${side}`}>
      {empty ? (
        <div className="compare__line compare__line--empty">
          <span className="compare__num" />
          <span className="compare__text">·</span>
        </div>
      ) : (
        lines.map((line, i) => (
          <div key={i} className="compare__line">
            <span className="compare__num">{nums[i]}</span>
            <span className="compare__text">
              {kind === "replace" && wordDiff ? (
                <Segments segs={wordDiff[i]} side={side} />
              ) : (
                line || "\u00A0"
              )}
            </span>
          </div>
        ))
      )}
    </div>
  );
}

function Segments({ segs, side }: { segs: WordDiffSeg[]; side: "left" | "right" }) {
  return (
    <>
      {segs.map((seg, i) => (
        <span
          key={i}
          className={
            seg.kind === "same"
              ? "cmp-w"
              : seg.kind === "add"
                ? side === "right"
                  ? "cmp-w cmp-w--add"
                  : "cmp-w"
                : side === "left"
                  ? "cmp-w cmp-w--del"
                  : "cmp-w"
          }
        >
          {seg.text}
        </span>
      ))}
    </>
  );
}
