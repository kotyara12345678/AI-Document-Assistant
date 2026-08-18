/**
 * Word-level diff for a single pair of changed lines (side-by-side compare).
 *
 * The backend already computes the *line*-level diff; this module only splits
 * the inside of one changed line into tokens so the UI can highlight exactly
 * which words were added/removed, like GitHub's word-diff.
 */

export type WordDiffKind = "same" | "add" | "del";

export interface WordDiffSeg {
  kind: WordDiffKind;
  text: string;
}

const TOKEN_RE = /(\s+|[()\[\]{}.,;:!?«»"'—–-]+)/;

function tokenize(text: string): string[] {
  return text.split(TOKEN_RE).filter((t) => t.length > 0);
}

function lcsTokens(a: string[], b: string[]): number[][] {
  const n = a.length;
  const m = b.length;
  const dp: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      dp[i][j] =
        a[i] === b[j]
          ? dp[i + 1][j + 1] + 1
          : Math.max(dp[i + 1][j], dp[i][j + 1]);
    }
  }
  return dp;
}

/**
 * Diff two lines token-by-token (LCS).
 *
 * Returns aligned segments for the left ("old") and right ("new") sides:
 * a shared token is "same" on both; a token only in the left is "del";
 * a token only in the right is "add".
 */
export function diffLines(left: string, right: string): {
  leftSegs: WordDiffSeg[];
  rightSegs: WordDiffSeg[];
} {
  if (left === right) {
    return {
      leftSegs: [{ kind: "same", text: left }],
      rightSegs: [{ kind: "same", text: right }],
    };
  }

  const a = tokenize(left);
  const b = tokenize(right);
  if (a.length === 0) {
    return {
      leftSegs: [{ kind: "del", text: left }],
      rightSegs: b.map((t) => ({ kind: "add", text: t })),
    };
  }
  if (b.length === 0) {
    return {
      leftSegs: a.map((t) => ({ kind: "del", text: t })),
      rightSegs: [{ kind: "add", text: right }],
    };
  }

  const dp = lcsTokens(a, b);
  const leftSegs: WordDiffSeg[] = [];
  const rightSegs: WordDiffSeg[] = [];
  let i = 0;
  let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) {
      leftSegs.push({ kind: "same", text: a[i] });
      rightSegs.push({ kind: "same", text: b[j] });
      i += 1;
      j += 1;
    } else if (dp[i + 1][j] >= dp[i][j + 1]) {
      leftSegs.push({ kind: "del", text: a[i] });
      i += 1;
    } else {
      rightSegs.push({ kind: "add", text: b[j] });
      j += 1;
    }
  }
  while (i < a.length) {
    leftSegs.push({ kind: "del", text: a[i] });
    i += 1;
  }
  while (j < b.length) {
    rightSegs.push({ kind: "add", text: b[j] });
    j += 1;
  }

  return { leftSegs, rightSegs };
}

/** Collapse consecutive same-kind segments (keeps the rendered DOM small). */
export function mergeSegs(segs: WordDiffSeg[]): WordDiffSeg[] {
  const out: WordDiffSeg[] = [];
  for (const seg of segs) {
    const last = out[out.length - 1];
    if (last && last.kind === seg.kind) {
      last.text += seg.text;
    } else {
      out.push({ kind: seg.kind, text: seg.text });
    }
  }
  return out;
}
