import { describe, expect, it, vi, beforeEach } from "vitest";
import { screen, fireEvent } from "@testing-library/react";
import CopyableBlock, { extractCodeBlock } from "../codeBlock";
import { renderWithI18n } from "../test/render";

describe("extractCodeBlock", () => {
  it("returns the content of a single fenced block", () => {
    const r = extractCodeBlock("```\nДоговор №1\nУсловия.\n```");
    expect(r).not.toBeNull();
    expect(r!.code).toBe("Договор №1\nУсловия.");
    expect(r!.before).toBe("");
    expect(r!.after).toBe("");
  });

  it("keeps a language tag and trims surrounding whitespace", () => {
    const r = extractCodeBlock("\n```docx\n  contract body  \n```\n");
    expect(r).not.toBeNull();
    expect(r!.code).toBe("  contract body  ");
  });

  it("returns null for plain text without a fence", () => {
    expect(extractCodeBlock("Привет, чем могу помочь?")).toBeNull();
  });

  it("returns null when there are multiple fenced blocks", () => {
    const text = "```\na\n```\n\n```\nb\n```";
    expect(extractCodeBlock(text)).toBeNull();
  });

  it("returns null when the block contains a nested fence", () => {
    const text = "```\nouter\n```\ninner\n```\n";
    expect(extractCodeBlock(text)).toBeNull();
  });

  it("extracts leading/trailing notes around a single block (lenient)", () => {
    const r = extractCodeBlock("Смотри:\n```\nкод\n```\nСохранено.");
    expect(r).not.toBeNull();
    expect(r!.before).toBe("Смотри:");
    expect(r!.code).toBe("код");
    expect(r!.after).toBe("Сохранено.");
  });
});

describe("CopyableBlock", () => {
  beforeEach(() => {
    Object.assign(navigator, {
      clipboard: { writeText: vi.fn().mockResolvedValue(undefined) },
    });
  });

  it("renders the code and a copy button, and copies on click", async () => {
    const code = "line one\nline two";
    renderWithI18n(<CopyableBlock result={{ before: "", code, after: "" }} />);
    expect(screen.getByText(/line one/)).toBeTruthy();
    const btn = screen.getByText("Копировать");
    fireEvent.click(btn);
    expect(navigator.clipboard.writeText).toHaveBeenCalledWith(code);
  });
});
