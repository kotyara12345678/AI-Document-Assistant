export interface HighlightSegment {
  text: string;
  hit: boolean;
}

function escapeRegExp(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Split `text` into segments, marking every occurrence of any string in
 * `highlights` (case-insensitive).
 *
 * Matching is whitespace-tolerant: any run of whitespace inside a needle
 * matches any run of whitespace inside `text`. A retrieved chunk is normalized
 * to single spaces by the chunker, so without this it would never be found
 * verbatim in the original document text with its line breaks/blank lines.
 *
 * Overlapping matches are merged so each character is highlighted at most once.
 */
export function highlightSegments(text: string, highlights: string[]): HighlightSegment[] {
  const needles = highlights.map((h) => h.trim()).filter((h) => h.length > 0);
  if (needles.length === 0) return [{ text, hit: false }];

  const ranges: Array<[number, number]> = [];

  for (const needle of needles) {
    const pattern = escapeRegExp(needle).replace(/\s+/g, "\\s+");
    const re = new RegExp(pattern, "gi");
    let match: RegExpExecArray | null;
    while ((match = re.exec(text)) !== null) {
      if (match[0].length === 0) {
        // Zero-width guard: never get stuck on an empty match.
        re.lastIndex += 1;
        continue;
      }
      ranges.push([match.index, match.index + match[0].length]);
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