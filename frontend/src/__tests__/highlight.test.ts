import { describe, expect, it } from "vitest";
import { highlightSegments } from "../highlight";
import { normalizeContent } from "../components/FileViewer";

describe("highlightSegments whitespace tolerance", () => {
  it("matches a chunk across the newlines and extra spaces of raw text", () => {
    const doc = "Первая строка.\nВторая строка   с   лишними пробелами.\n\nТретья часть.";
    const chunk = "Первая строка. Вторая строка с лишними пробелами.";
    const segs = highlightSegments(doc, [chunk]);
    const hit = segs.filter((s) => s.hit).map((s) => s.text).join("");
    expect(hit).toContain("Первая строка.");
    expect(hit).toContain("Вторая строка");
    expect(hit).toContain("лишними пробелами.");
  });

  it("returns the whole text as non-hit when there are no needles", () => {
    expect(highlightSegments("hello world", [])).toEqual([{ text: "hello world", hit: false }]);
  });

  it("matches case-insensitively", () => {
    const segs = highlightSegments("ХВОСТ хвост", ["хвост"]);
    const hits = segs.filter((s) => s.hit).map((s) => s.text);
    expect(hits).toEqual(["ХВОСТ", "хвост"]);
  });

  it("merges overlapping matches from different needles into one highlight", () => {
    const hits = highlightSegments("abcdef", ["abc", "cde"])
      .filter((s) => s.hit)
      .map((s) => s.text)
      .join("");
    expect(hits).toBe("abcde");
  });
});

describe("normalizeContent", () => {
  it("collapses horizontal spaces and runs of blank lines", () => {
    expect(normalizeContent("a   b\n\n\n\nc  d")).toBe("a b\n\nc d");
  });

  it("strips spaces around line breaks and trims the edges", () => {
    expect(normalizeContent("  x \n\n  y  ")).toBe("x\n\ny");
  });
});