import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, screen } from "@testing-library/react";
import DocsPage from "../DocsPage";
import { renderWithI18n } from "../../test/render";

class IntersectionObserverStub {
  constructor(_cb: IntersectionObserverCallback, _opts?: IntersectionObserverInit) {}
  observe(_target: Element) {}
  unobserve(_target: Element) {}
  disconnect() {}
  takeRecords(): IntersectionObserverEntry[] {
    return [];
  }
  readonly root = null;
  readonly rootMargin = "";
  readonly thresholds = [0];
}

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", IntersectionObserverStub);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function renderPage() {
  return renderWithI18n(<DocsPage onHome={vi.fn()} />);
}

describe("DocsPage", () => {
  it("renders the brand and top navigation items", () => {
    renderPage();
    expect(screen.getAllByText("ADA").length).toBeGreaterThanOrEqual(1);
    const links = screen.getAllByRole("button");
    expect(links.map((l) => l.textContent ?? "").join(" ")).toContain("Обзор");
    expect(links.map((l) => l.textContent ?? "").join(" ")).toContain("Как пользоваться");
    expect(links.map((l) => l.textContent ?? "").join(" ")).toContain("Преимущества");
    expect(links.map((l) => l.textContent ?? "").join(" ")).toContain("Развитие");
  });

  it("renders key documentation sections", () => {
    renderPage();
    expect(screen.getByText("Ваш помощник для работы с документами")).toBeTruthy();
    expect(screen.getByText("Начните с четырёх простых шагов")).toBeTruthy();
    expect(screen.getByText("Что умеет ADA")).toBeTruthy();
    expect(screen.getByText("Почему это удобно")).toBeTruthy();
    expect(screen.getByText("Ваши документы — только ваши")).toBeTruthy();
  });

  it("shows supported document formats", () => {
    renderPage();
    expect(screen.getByText("PDF")).toBeTruthy();
    expect(screen.getByText("DOCX")).toBeTruthy();
    expect(screen.getByText("ODT")).toBeTruthy();
    expect(screen.getByText("TXT")).toBeTruthy();
    expect(screen.getByText("Markdown")).toBeTruthy();
  });

  it("explains the steps to start using the service", () => {
    renderPage();
    const body = document.body.textContent ?? "";
    expect(body).toContain("Зарегистрируйтесь и войдите");
    expect(body).toContain("Загрузите документы");
    expect(body).toContain("Задайте вопрос");
  });
});