import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, screen, waitFor } from "@testing-library/react";
import ProfilePanel from "../ProfilePanel";
import type { UserOut } from "../../types";
import { renderWithI18n } from "../../test/render";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const alice: UserOut = {
  id: 1,
  email: "alice@example.com",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  avatar_url: null,
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, url: "", json: async () => body };
}

function stubFetch(routes: Record<string, (init?: RequestInit) => unknown>) {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = String(input);
      const key = url.replace(/^\/api/, "");
      const handler = routes[key];
      if (!handler) {
        return jsonResponse({ detail: `no stub for ${key}` }, 404);
      }
      return jsonResponse(await handler(init));
    }),
  );
}

beforeEach(() => {
  localStorage.setItem("docsearch-token", "token-123");
});

function renderPanel(props: Partial<React.ComponentProps<typeof ProfilePanel>> = {}) {
  return renderWithI18n(
    <ProfilePanel
      user={alice}
      onBack={() => {}}
      onUserUpdated={() => {}}
      theme="light"
      onToggleTheme={() => {}}
      onLogout={() => {}}
      {...props}
    />,
  );
}

describe("ProfilePanel", () => {
  it("renders profile info and goes back", () => {
    const onBack = vi.fn();
    const onUpdated = vi.fn();
    stubFetch({ "/auth/me/usage": () => ({ total_tokens: 0, tokens_today: 0, tokens_7d: 0, tokens_30d: 0, requests: 0 }) });
    renderPanel({ onBack, onUserUpdated: onUpdated });
    expect(screen.getByText("Личный кабинет")).toBeTruthy();
    expect(screen.getByText("Смена пароля")).toBeTruthy();
    fireEvent.click(screen.getByText("← Назад к чату"));
    expect(onBack).toHaveBeenCalled();
  });

  it("shows usage stats from the server", async () => {
    stubFetch({
      "/auth/me/usage": () => ({ total_tokens: 12345, tokens_today: 10, tokens_7d: 999, tokens_30d: 5000, requests: 7 }),
    });
    renderPanel();
    await waitFor(() => expect(screen.getByText("12 345")).toBeTruthy());
    expect(screen.getByText("999")).toBeTruthy(); // 7d = 999
    expect(screen.getByText("5 000")).toBeTruthy();
    expect(screen.getByText("7")).toBeTruthy();
  });

  it("rejects a too-short new password", async () => {
    stubFetch({ "/auth/me/usage": () => ({ total_tokens: 0, tokens_today: 0, tokens_7d: 0, tokens_30d: 0, requests: 0 }) });
    renderPanel();
    fireEvent.change(screen.getByPlaceholderText("Текущий пароль"), { target: { value: "old-pass-1" } });
    fireEvent.change(screen.getByPlaceholderText("Новый пароль (мин. 8 символов)"), { target: { value: "abc" } });
    fireEvent.change(screen.getByPlaceholderText("Повторите новый пароль"), { target: { value: "abc" } });
    fireEvent.click(screen.getByText("Изменить пароль"));
    expect(await screen.findByText("Пароль должен быть не короче 8 символов")).toBeTruthy();
  });

  it("changes the password via the API", async () => {
    const changeSpy = vi.fn((_init?: RequestInit) => ({ ...alice }));
    stubFetch({
      "/auth/me/usage": () => ({ total_tokens: 0, tokens_today: 0, tokens_7d: 0, tokens_30d: 0, requests: 0 }),
      "/auth/me/password": (init) => changeSpy(init),
    });
    const onUpdated = vi.fn();
    renderPanel({ onUserUpdated: onUpdated });
    fireEvent.change(screen.getByPlaceholderText("Текущий пароль"), { target: { value: "old-pass-1" } });
    fireEvent.change(screen.getByPlaceholderText("Новый пароль (мин. 8 символов)"), { target: { value: "new-pass-1" } });
    fireEvent.change(screen.getByPlaceholderText("Повторите новый пароль"), { target: { value: "new-pass-1" } });
    fireEvent.click(screen.getByText("Изменить пароль"));
    await waitFor(() => expect(changeSpy).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(screen.getByText("Пароль успешно изменён")).toBeTruthy());
    expect(onUpdated).toHaveBeenCalledWith(alice);
  });

  it("toggles theme and logs out", () => {
    stubFetch({ "/auth/me/usage": () => ({ total_tokens: 0, tokens_today: 0, tokens_7d: 0, tokens_30d: 0, requests: 0 }) });
    const onToggleTheme = vi.fn();
    const onLogout = vi.fn();
    renderPanel({ theme: "dark", onToggleTheme, onLogout });
    fireEvent.click(screen.getByText("☀️ Светлая"));
    expect(onToggleTheme).toHaveBeenCalled();
    fireEvent.click(screen.getByText("Выйти"));
    expect(onLogout).toHaveBeenCalled();
  });

  it("uploads an avatar as a data URL", async () => {
    const patchSpy = vi.fn((_init?: RequestInit) => ({ ...alice, avatar_url: "data:image/png;base64,AAA" }));
    stubFetch({
      "/auth/me/usage": () => ({ total_tokens: 0, tokens_today: 0, tokens_7d: 0, tokens_30d: 0, requests: 0 }),
      "/auth/me": (init) => patchSpy(init),
    });
    const onUpdated = vi.fn();
    renderPanel({ onUserUpdated: onUpdated });

    const file = new File(["x"], "avatar.png", { type: "image/png" });
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(patchSpy).toHaveBeenCalledTimes(1));
    const body = JSON.parse(String((patchSpy.mock.calls[0][0] as RequestInit | undefined)?.body ?? "{}"));
    expect(body.avatar_url).toMatch(/^data:image\/png;base64,/);
    await waitFor(() => expect(onUpdated).toHaveBeenCalled());
  });
});