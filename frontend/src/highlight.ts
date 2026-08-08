export interface HighlightSegment {
  text: string;
  hit: boolean;
}

/**
 * Split `text` into segments, marking every occurrence of any string in
 * `highlights` (case-insensitive). Overlapping matches are merged so each
 * character is highlighted at most once.
 */
export function highlightSegments(text: string, highlights: string[]): HighlightSegment[] {
  const needles = highlights.map((h) => h.trim()).filter((h) => h.length > 0);
  if (needles.length === 0) return [{ text, hit: false }];

  const lower = text.toLowerCase();
  const ranges: Array<[number, number]> = [];

  for (const needle of needles) {
    const n = needle.toLowerCase();
    let idx = lower.indexOf(n);
    while (idx !== -1) {
      ranges.push([idx, idx + needle.length]);
      idx = lower.indexOf(n, idx + needle.length);
    }
  }

  if (ranges.length === 0) return [{ text, hit: false }];

  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [];
  for (const r of ranges) {
    const last = merged[merged.length - 1];
    if (last && r[0] <= last[1]) {
      last[1] = Math.max(last[1], r[1]);
    } else {
      merged.push([r[0], r[1]]);
    }
  }

  const segments: HighlightSegment[] = [];
  let pos = 0;
  for (const [start, end] of merged) {
    if (start > pos) segments.push({ text: text.slice(pos, start), hit: false });
    segments.push({ text: text.slice(start, end), hit: true });
    pos = end;
  }
  if (pos < text.length) segments.push({ text: text.slice(pos), hit: false });

  return segments;
}
