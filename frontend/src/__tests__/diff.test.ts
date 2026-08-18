import { describe, expect, it } from "vitest";
import { diffLines, mergeSegs } from "../diff";

describe("diffLines", () => {
  it("marks identical lines as same", () => {
    const { leftSegs, rightSegs } = diffLines("hello world", "hello world");
    expect(leftSegs).toEqual([{ kind: "same", text: "hello world" }]);
    expect(rightSegs).toEqual([{ kind: "same", text: "hello world" }]);
  });

  it("highlights a changed word as del on the left and add on the right", () => {
    const { leftSegs, rightSegs } = diffLines("salary is 1000 rubles", "salary is 2000 rubles");
    const leftText = mergeSegs(leftSegs).map((s) => s.text).join("");
    const rightText = mergeSegs(rightSegs).map((s) => s.text).join("");
    expect(leftText).toBe("salary is 1000 rubles");
    expect(rightText).toBe("salary is 2000 rubles");
    expect(leftSegs.some((s) => s.kind === "del" && s.text === "1000")).toBe(true);
    expect(rightSegs.some((s) => s.kind === "add" && s.text === "2000")).toBe(true);
  });

  it("marks a removed word as del only", () => {
    const { leftSegs, rightSegs } = diffLines("red green blue", "red blue");
    expect(leftSegs.some((s) => s.kind === "del" && s.text === "green")).toBe(true);
    expect(rightSegs.every((s) => s.kind === "same" || s.kind === "add")).toBe(true);
  });

  it("marks an added word as add only", () => {
    const { leftSegs, rightSegs } = diffLines("red blue", "red green blue");
    expect(rightSegs.some((s) => s.kind === "add" && s.text === "green")).toBe(true);
    expect(leftSegs.every((s) => s.kind === "same" || s.kind === "del")).toBe(true);
  });

  it("handles an empty right side", () => {
    const { leftSegs, rightSegs } = diffLines("only left", "");
    expect(mergeSegs(leftSegs).map((s) => s.text).join("")).toBe("only left");
    expect(leftSegs.some((s) => s.kind === "del")).toBe(true);
    expect(rightSegs).toHaveLength(1);
    expect(rightSegs[0].kind).toBe("add");
  });

  it("handles an empty left side", () => {
    const { leftSegs, rightSegs } = diffLines("", "only right");
    expect(mergeSegs(rightSegs).map((s) => s.text).join("")).toBe("only right");
    expect(rightSegs.some((s) => s.kind === "add")).toBe(true);
    expect(leftSegs).toHaveLength(1);
    expect(leftSegs[0].kind).toBe("del");
  });

  it("handles both empty", () => {
    const { leftSegs, rightSegs } = diffLines("", "");
    expect(mergeSegs(leftSegs).map((s) => s.text).join("")).toBe("");
    expect(mergeSegs(rightSegs).map((s) => s.text).join("")).toBe("");
  });

  it("handles cyrillic text", () => {
    const { leftSegs, rightSegs } = diffLines(
      "Зарплата Сергея 1000 рублей",
      "Зарплата Сергея 2000 рублей"
    );
    expect(leftSegs.some((s) => s.kind === "del" && s.text === "1000")).toBe(true);
    expect(rightSegs.some((s) => s.kind === "add" && s.text === "2000")).toBe(true);
  });
});

describe("mergeSegs", () => {
  it("collapses adjacent same-kind segments", () => {
    const segs = [
      { kind: "same" as const, text: "a" },
      { kind: "same" as const, text: " b" },
      { kind: "del" as const, text: "x" },
      { kind: "del" as const, text: " y" },
      { kind: "add" as const, text: "z" },
    ];
    expect(mergeSegs(segs)).toEqual([
      { kind: "same", text: "a b" },
      { kind: "del", text: "x y" },
      { kind: "add", text: "z" },
    ]);
  });
});
