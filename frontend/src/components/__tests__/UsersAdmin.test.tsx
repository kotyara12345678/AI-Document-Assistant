import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import UsersAdmin from "../UsersAdmin";

afterEach(() => {
  localStorage.clear();
  cleanup();
  vi.unstubAllGlobals();
});

const alice = {
  id: 1,
  email: "alice@example.com",
  role: "user",
  created_at: "2026-01-01T00:00:00Z",
  last_active_at: "2026-08-01T12:00:00Z",
  is_active: true,
  is_deleted: false,
  reports_active: 0,
};
const bob = {
  id: 2,
  email: "bob@example.com",
  role: "moderator",
  created_at: "2026-02-01T00:00:00Z",
  last_active_at: null,
  is_active: false,
  is_deleted: false,
  reports_active: 2,
};
const charlie = {
  id: 3,
  email: "charlie@example.com",
  role: "user",
  created_at: "2026-03-01T00:00:00Z",
  last_active_at: "2026-08-02T08:30:00Z",
  is_active: true,
  is_deleted: false,
  reports_active: 0,
};

function jsonResponse(body: unknown, status = 200) {
  return { ok: status >= 200 && status < 300, status, url: "", json: async () => body };
}

/** fetch stub that routes by URL the way the frontend api client calls it. */
function stubApi(extra?: (url: string, init?: RequestInit) => unknown) {
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = String(input);
    if (extra) {
      const hit = extra(url, init);
      if (hit !== undefined) return hit;
    }
    if (url.endsWith("/role")) {
      const body = JSON.parse(String(init?.body)) as { role: string };
      return jsonResponse({ ...charlie, role: body.role });
    }
    if (url.endsWith("/status")) {
      const body = JSON.parse(String(init?.body)) as { is_active: boolean };
      return jsonResponse({ ...charlie, is_active: body.is_active });
    }
    if (url.includes("/users/2/reports")) {
      return jsonResponse({
        items: [
          {
            id: 11,
            reporter_email: "mallory@example.com",
            reported_user_id: 2,
            reason: "spam",
            description: "Спам в чате",
            status: "pending",
            created_at: "2026-08-10T00:00:00Z",
            resolved_at: null,
            resolved_by_email: null,
          },
        ],
        total: 1,
        page: 1,
        limit: 20,
      });
    }
    if (url.startsWith("/api/admin/users/") && init?.method === "DELETE") {
      return jsonResponse({ deleted: true, user_id: 3 });
    }
    if (url.startsWith("/api/admin/users")) {
      return jsonResponse({ items: [charlie, bob, alice], total: 3, page: 1, limit: 20 });
    }
    throw new Error(`unmocked admin URL: ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderUsers() {
  const utils = render(<UsersAdmin currentUserId={1} />);
  await waitFor(() => expect(screen.queryAllByText("charlie@example.com").length).toBeGreaterThan(0));
  return utils;
}

describe("UsersAdmin", () => {
  it("renders the user table with roles, statuses and self marker", async () => {
    stubApi();
    await renderUsers();

    expect(screen.queryAllByText("charlie@example.com").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("bob@example.com").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("alice@example.com").length).toBeGreaterThan(0);
    // roles badges + statuses
    expect(screen.queryAllByText("Модератор").length).toBeGreaterThan(0);
    expect(screen.queryAllByText("Заблокирован").length).toBeGreaterThan(0);
    expect(screen.queryByText(/\(вы\)/)).toBeTruthy(); // self is marked
    expect(screen.queryByText("Показаны все")).toBeTruthy(); // 3 == total, no more pages
  });

  it("assigns a role through the ⋮ menu (moderator cannot be raised by UI here)", async () => {
    const fetchMock = stubApi();
    await renderUsers();

    const menuButtons = screen.getAllByRole("button", { name: "Действия" });
    fireEvent.click(menuButtons[0]); // charlie is the first row (mock order)
    fireEvent.click(screen.getByText("Сделать модератором"));

    await waitFor(() => {
      const call = fetchMock.mock.calls.find(([, init]) => String(init?.method) === "PATCH");
      expect(call).toBeTruthy();
    });
    const call = fetchMock.mock.calls.find(([, i]) => String(i?.method) === "PATCH") as [
      string,
      RequestInit,
    ];
    expect(call[0]).toBe("/api/admin/users/3/role");
    expect(JSON.parse(String(call[1].body))).toEqual({ role: "moderator" });
    await waitFor(() => expect(screen.queryAllByText("Модератор").length).toBeGreaterThan(1));
  });

  it("requires confirmation before deleting and removes the row afterwards", async () => {
    const fetchMock = stubApi();
    await renderUsers();

    const menuButtons = screen.getAllByRole("button", { name: "Действия" });
    fireEvent.click(menuButtons[0]);
    fireEvent.click(screen.getByText("Удалить пользователя"));

    // dangerous action never fires without confirmation
    expect(fetchMock.mock.calls.some(([, i]) => String(i?.method) === "DELETE")).toBe(false);
    expect(screen.getByText("Удалить пользователя")).toBeTruthy(); // confirm modal title

    fireEvent.click(screen.getByRole("button", { name: "Удалить" }));
    await waitFor(() => expect(fetchMock.mock.calls.some(([, i]) => String(i?.method) === "DELETE")).toBe(true));
    await waitFor(() => expect(screen.queryAllByText("charlie@example.com").length).toBe(0));
    expect(screen.queryByText(/пользователь charlie@example\.com удалён/i)).toBeTruthy();
  });

  it("confirms blocking and surfaces the blocked status", async () => {
    const fetchMock = stubApi();
    await renderUsers();

    const menuButtons = screen.getAllByRole("button", { name: "Действия" });
    fireEvent.click(menuButtons[0]);
    fireEvent.click(screen.getByText("Заблокировать"));
    expect(screen.getByText("Заблокировать пользователя")).toBeTruthy(); // confirm modal title
    expect(fetchMock.mock.calls.some(([, i]) => String(i?.method) === "PATCH")).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Заблокировать" }));
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, i]) => String(i?.method) === "PATCH")).toBe(true),
    );
    await waitFor(() => expect(screen.queryAllByText("Заблокирован").length).toBeGreaterThan(1));
  });

  it("opens the reports modal from the report count and renders complaint details", async () => {
    stubApi();
    await renderUsers();

    fireEvent.click(screen.getByRole("button", { name: "2" })); // bob's active reports
    await waitFor(() => expect(screen.queryByText(/Жалобы на пользователя bob@example\.com/)).toBeTruthy());
    await waitFor(() => expect(screen.queryByText("mallory@example.com")).toBeTruthy());
    expect(screen.queryByText("Спам в чате")).toBeTruthy();
    expect(screen.queryByText("На рассмотрении")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Закрыть" }));
    await waitFor(() => expect(screen.queryByText("mallory@example.com")).toBeNull());
  });
});