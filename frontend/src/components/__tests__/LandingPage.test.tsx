import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import LandingPage from "../LandingPage";
import { renderWithI18n } from "../../test/render";

afterEach(() => {
  cleanup();
});

function renderPage(overrides: Partial<Parameters<typeof LandingPage>[0]> = {}) {
  const props = {
    onLogin: vi.fn(),
    onRegister: vi.fn(),
    onOpenPrivacy: vi.fn(),
    onOpenCookies: vi.fn(),
    onOpenCookieSettings: vi.fn(),
    onOpenDocs: vi.fn(),
    ...overrides,
  };
  return { ...renderWithI18n(<LandingPage {...props} />), props };
}

describe("LandingPage", () => {
  it("renders the hero, description and CTA buttons", () => {
    renderPage();
    expect(screen.getByText("ADA", { selector: "h1" })).toBeTruthy();
    expect(screen.getByText("AI Document Assistant")).toBeTruthy();
    expect(screen.getByText(/Загружайте документы\./)).toBeTruthy();
    expect(screen.getAllByText("Начать работу").length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText("Возможности").length).toBeGreaterThanOrEqual(1);
  });

  it("renders all six feature cards from the specification", () => {
    renderPage();
    expect(screen.getByText("Работа с документами")).toBeTruthy();
    expect(screen.getByText("AI-поиск")).toBeTruthy();
    expect(screen.getByText("Анализ документов")).toBeTruthy();
    expect(screen.getByText("Редактирование")).toBeTruthy();
    expect(screen.getByText("Генерация документов")).toBeTruthy();
    expect(screen.getByText("Мгновенный ответ")).toBeTruthy();
  });

  it("renders the final CTA block", () => {
    renderPage();
    expect(screen.getByText("Умный поиск в ваших документах за секунды.")).toBeTruthy();
  });

  it("Registration button in the navigation starts registration", () => {
    const { props } = renderPage();
    fireEvent.click(screen.getByText("Регистрация"));
    expect(props.onRegister).toHaveBeenCalledTimes(1);
  });

  it("Войти buttons forward to login", () => {
    const { props } = renderPage();
    fireEvent.click(screen.getByText("Войти"));
    expect(props.onLogin).toHaveBeenCalledTimes(1);
  });

  it("footer links open policy pages", () => {
    const { props } = renderPage();
    fireEvent.click(screen.getByText("Политика обработки персональных данных"));
    expect(props.onOpenPrivacy).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("Cookie Policy"));
    expect(props.onOpenCookies).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByText("Настроить cookie"));
    expect(props.onOpenCookieSettings).toHaveBeenCalledTimes(1);
  });

  it("opens the documentation page from the navigation", () => {
    const { props } = renderPage();
    fireEvent.click(screen.getAllByText("Документация")[0]);
    expect(props.onOpenDocs).toHaveBeenCalledTimes(1);
  });
});