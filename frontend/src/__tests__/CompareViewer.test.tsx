import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import CompareViewer from "../components/CompareViewer";
import type { CompareResponse } from "../types";

function makeData(overrides: Partial<CompareResponse> = {}): CompareResponse {
  return {
    left: {
      id: 1,
      original_filename: "left.txt",
      file_type: "txt",
      content_length: 100,
      created_at: "2026-01-01T00:00:00",
      source_file_id: null,
    },
    right: {
      id: 2,
      original_filename: "right.txt",
      file_type: "txt",
      content_length: 120,
      created_at: "2026-01-01T00:00:00",
      source_file_id: 1,
    },
    left_lines: ["line a", "old line", "shared"],
    right_lines: ["line a", "new line", "shared"],
    operations: [
      { kind: "equal", left_start: 0, left_end: 1, right_start: 0, right_end: 1 },
      { kind: "replace", left_start: 1, left_end: 2, right_start: 1, right_end: 2 },
      { kind: "equal", left_start: 2, left_end: 3, right_start: 2, right_end: 3 },
    ],
    summary: { added_lines: 1, removed_lines: 1, changed_lines: 1, unchanged_lines: 2 },
    equal: false,
    truncated: false,
    limit: 4000,
    ...overrides,
  };
}

describe("CompareViewer", () => {
  it("renders the file names and summary", () => {
    render(<CompareViewer data={makeData()} />);
    expect(screen.getByText("+1 добавлено")).toBeTruthy();
    expect(screen.getByText("−1 удалено")).toBeTruthy();
    expect(screen.getByText("~1 изменено")).toBeTruthy();
    expect(screen.getByText("=2 без изменений")).toBeTruthy();
  });

  it("shows an empty state when documents are identical", () => {
    render(<CompareViewer data={makeData({ equal: true })} />);
    expect(screen.getByText("Тексты документов совпадают")).toBeTruthy();
  });

  it("highlights changed and shared lines", () => {
    const { container } = render(<CompareViewer data={makeData()} />);
    const rows = container.querySelectorAll(".compare__row");
    // equal + replace + equal
    expect(rows.length).toBe(3);
    expect(container.querySelector(".compare__row--replace")).toBeTruthy();
    expect(container.querySelectorAll(".cmp-w--del").length).toBeGreaterThan(0);
    expect(container.querySelectorAll(".cmp-w--add").length).toBeGreaterThan(0);
  });

  it("shows the truncation warning when diff was capped", () => {
    render(<CompareViewer data={makeData({ truncated: true })} />);
    expect(screen.getByText(/показаны первые 4000 строк/)).toBeTruthy();
  });
});