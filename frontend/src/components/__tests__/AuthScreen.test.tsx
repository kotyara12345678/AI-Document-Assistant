import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen } from "@testing-library/react";
import AuthScreen from "../AuthScreen";
import { renderWithI18n } from "../../test/render";

afterEach(() => {
  localStorage.clear();
  cleanup();
  vi.unstubAllGlobals();
});

describe("AuthScreen registration consent", () => {
  it("blocks registration until consent to personal data processing is given", () => {
    const onAuthed = vi.fn();
    const { container } = renderWithI18n(<AuthScreen initialMode="register" onAuthed={onAuthed} />);

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("Минимум 8 символов"), {
      target: { value: "secret123" },
    });
    fireEvent.change(screen.getByPlaceholderText("Ещё раз тот же пароль"), {
      target: { value: "secret123" },
    });

    fireEvent.click(screen.getByRole("button", { name: "Создать аккаунт" }));

    expect(screen.getByText("Необходимо согласие на обработку персональных данных")).toBeTruthy();
    expect(onAuthed).not.toHaveBeenCalled();

    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    expect(box.checked).toBe(false);
  });

  it("registers only after the consent checkbox is checked", async () => {
    const onAuthed = vi.fn();

    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 201,
      url: "/api/auth/register",
      json: async () => ({
        access_token: "token",
        token_type: "bearer",
        user: { id: 1, email: "user@example.com", role: "user", is_active: true },
      }),
    });
    vi.stubGlobal("fetch", fetchMock);

    const { container } = renderWithI18n(<AuthScreen initialMode="register" onAuthed={onAuthed} />);

    fireEvent.change(screen.getByPlaceholderText("you@example.com"), {
      target: { value: "user@example.com" },
    });
    fireEvent.change(screen.getByPlaceholderText("Минимум 8 символов"), {
      target: { value: "secret123" },
    });
    fireEvent.change(screen.getByPlaceholderText("Ещё раз тот же пароль"), {
      target: { value: "secret123" },
    });

    const box = container.querySelector('input[type="checkbox"]') as HTMLInputElement;
    fireEvent.click(box);
    expect(box.checked).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Создать аккаунт" }));

    await vi.waitFor(() => expect(onAuthed).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    const body = JSON.parse((init.body as string) ?? "{}");
    expect(body.email).toBe("user@example.com");
  });

  it("policy link opens the privacy page", () => {
    const onOpenPrivacy = vi.fn();
    renderWithI18n(<AuthScreen initialMode="register" onAuthed={vi.fn()} onOpenPrivacy={onOpenPrivacy} />);

    fireEvent.click(screen.getByText("Политикой обработки персональных данных"));
    expect(onOpenPrivacy).toHaveBeenCalledTimes(1);
  });

  it("mentions cookies with a link to Cookie Policy", () => {
    const onOpenCookies = vi.fn();
    renderWithI18n(<AuthScreen onAuthed={vi.fn()} onOpenCookies={onOpenCookies} />);

    expect(screen.getByText(/Мы используем файлы cookie/)).toBeTruthy();
    fireEvent.click(screen.getByText("Cookie Policy"));
    expect(onOpenCookies).toHaveBeenCalledTimes(1);
  });
});