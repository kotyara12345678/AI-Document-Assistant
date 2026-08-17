import { useCallback, useEffect, useState } from "react";
import {
  deleteAdminUser,
  fetchAdminUserReports,
  fetchAdminUsers,
  patchAdminUserRole,
  patchAdminUserStatus,
} from "../api";
import type {
  AdminReport,
  AdminReportStatus,
  AdminUser,
  UserRole,
} from "../types";

const PAGE_SIZE = 20;

const ROLE_LABELS: Record<UserRole, string> = {
  user: "Пользователь",
  moderator: "Модератор",
  admin: "Администратор",
};

const STATUS_LABELS: Record<AdminReportStatus, string> = {
  pending: "На рассмотрении",
  reviewed: "Рассмотрена",
  rejected: "Отклонена",
  action_taken: "Меры приняты",
};

interface ConfirmState {
  kind: "block" | "delete";
  user: AdminUser;
}

interface ReportsState {
  user: AdminUser;
  reports: AdminReport[];
  total: number;
  page: number;
  loading: boolean;
  error: string | null;
}

interface Props {
  currentUserId: number;
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    year: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function UsersAdmin({ currentUserId }: Props) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [menuFor, setMenuFor] = useState<number | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [reports, setReports] = useState<ReportsState | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setAppliedSearch(search.trim()), 400);
    return () => window.clearTimeout(t);
  }, [search]);

  const flashNotice = useCallback((msg: string) => {
    setNotice(msg);
    window.setTimeout(() => setNotice(null), 4000);
  }, []);

  const loadPage = useCallback(
    async (page: number, q: string, replace: boolean) => {
      if (replace) {
        setLoading(true);
      } else {
        setLoadingMore(true);
      }
      setError(null);
      try {
        const data = await fetchAdminUsers({ page, limit: PAGE_SIZE, search: q || undefined });
        setTotal(data.total);
        setUsers((prev) => (replace ? data.items : [...prev, ...data.items]));
      } catch (err) {
        setError(err instanceof Error ? err.message : "Не удалось загрузить пользователей");
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [],
  );

  // Initial load + reload whenever the (debounced) search term changes.
  useEffect(() => {
    void loadPage(1, appliedSearch, true);
  }, [appliedSearch, loadPage]);

  const replaceRow = useCallback((updated: AdminUser) => {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }, []);

  const applyError = useCallback((err: unknown): string => {
    return err instanceof Error ? err.message : "Не удалось выполнить операцию";
  }, []);

  const handleRole = useCallback(
    async (user: AdminUser, role: UserRole) => {
      setMenuFor(null);
      setError(null);
      try {
        const updated = await patchAdminUserRole(user.id, role);
        replaceRow(updated);
        flashNotice(`Роль пользователя ${user.email} изменена на «${ROLE_LABELS[role]}»`);
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice, replaceRow],
  );

  const handleBlockUnblock = useCallback(
    async (user: AdminUser) => {
      setConfirm(null);
      setMenuFor(null);
      setError(null);
      try {
        const updated = await patchAdminUserStatus(user.id, !user.is_active);
        replaceRow(updated);
        flashNotice(updated.is_active ? "Пользователь разблокирован" : "Пользователь заблокирован");
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice, replaceRow],
  );

  const handleDelete = useCallback(
    async (user: AdminUser) => {
      setConfirm(null);
      setMenuFor(null);
      setError(null);
      try {
        await deleteAdminUser(user.id);
        setUsers((prev) => prev.filter((u) => u.id !== user.id));
        setTotal((t) => Math.max(0, t - 1));
        flashNotice(`Пользователь ${user.email} удалён`);
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice],
  );

  const openReports = useCallback(async (user: AdminUser) => {
    setMenuFor(null);
    setError(null);
    setReports({ user, reports: [], total: 0, page: 0, loading: true, error: null });
    try {
      const data = await fetchAdminUserReports(user.id, 1, PAGE_SIZE);
      setReports({ user, reports: data.items, total: data.total, page: 1, loading: false, error: null });
    } catch (err) {
      setReports({
        user,
        reports: [],
        total: 0,
        page: 1,
        loading: false,
        error: err instanceof Error ? err.message : "Не удалось загрузить жалобы",
      });
    }
  }, []);

  const loadMoreReports = useCallback(async () => {
    if (!reports || reports.loading) return;
    const nextPage = reports.page + 1;
    setReports((r) => (r ? { ...r, loading: true } : r));
    try {
      const data = await fetchAdminUserReports(reports.user.id, nextPage, PAGE_SIZE);
      setReports((r) =>
        r
          ? {
              ...r,
              reports: [...r.reports, ...data.items],
              total: data.total,
              page: nextPage,
              loading: false,
            }
          : r,
      );
    } catch (err) {
      setReports((r) =>
        r ? { ...r, loading: false, error: applyError(err) } : r,
      );
    }
  }, [reports, applyError]);

  const hasMore = users.length < total;

  return (
    <section className="admin__section">
      <div className="users-toolbar">
        <input
          className="users-search"
          type="search"
          placeholder="Поиск по имени / email…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="users-count">
          {total.toLocaleString("ru-RU")} {plural(total, "пользователь", "пользователя", "пользователей")}
        </span>
      </div>

      {error && <div className="admin__error">{error}</div>}
      {notice && <div className="admin__notice">{notice}</div>}
      {loading && <div className="admin__loading">Загружаем пользователей…</div>}

      {!loading && (
        <>
          {users.length === 0 ? (
            <div className="admin__loading">Пользователи не найдены.</div>
          ) : (
            <div className="users-scroll">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Пользователь</th>
                    <th>Email</th>
                    <th>Регистрация</th>
                    <th>Роль</th>
                    <th>Жалобы</th>
                    <th>Статус</th>
                    <th>Активность</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {users.map((u) => {
                    const isSelf = u.id === currentUserId;
                    return (
                      <tr key={u.id}>
                        <td className="admin-table__muted">{u.id}</td>
                        <td className="admin-table__name" title={u.email}>
                          {u.email}
                          {isSelf && <span className="users-self"> (вы)</span>}
                        </td>
                        <td className="admin-table__email">{u.email}</td>
                        <td className="admin-table__muted">{formatDate(u.created_at)}</td>
                        <td>
                          <span className={`pill pill--role pill--role-${u.role}`}>
                            {ROLE_LABELS[u.role]}
                          </span>
                        </td>
                        <td>
                          {u.reports_active > 0 ? (
                            <button className="users-reports-link" onClick={() => void openReports(u)}>
                              {u.reports_active}
                            </button>
                          ) : (
                            <span className="admin-table__muted">0</span>
                          )}
                        </td>
                        <td>
                          <span className={`pill ${u.is_active ? "pill--ok" : "pill--down"}`}>
                            {u.is_active ? "Активен" : "Заблокирован"}
                          </span>
                        </td>
                        <td className="admin-table__muted">{formatDate(u.last_active_at)}</td>
                        <td>
                          <div className="users-menu">
                            {menuFor === u.id && <div className="users-menu-backdrop" onClick={() => setMenuFor(null)} />}
                            <button
                              className="users-menu-btn"
                              onClick={() => setMenuFor((cur) => (cur === u.id ? null : u.id))}
                              aria-label="Действия"
                              title="Действия"
                            >
                              ⋮
                            </button>
                            {menuFor === u.id && (
                              <div className="users-menu-list">
                                <button className="users-menu-item" onClick={() => void openReports(u)}>
                                  Посмотреть жалобы
                                </button>
                                {!isSelf && u.role !== "admin" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "admin")}>
                                    Сделать администратором
                                  </button>
                                )}
                                {!isSelf && u.role !== "moderator" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "moderator")}>
                                    Сделать модератором
                                  </button>
                                )}
                                {!isSelf && u.role === "admin" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "user")}>
                                    Снять права администратора
                                  </button>
                                )}
                                {!isSelf && u.role === "moderator" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "user")}>
                                    Снять права модератора
                                  </button>
                                )}
                                {!isSelf && !u.is_active && (
                                  <button className="users-menu-item" onClick={() => void handleBlockUnblock(u)}>
                                    Разблокировать
                                  </button>
                                )}
                                {!isSelf && u.is_active && (
                                  <button
                                    className="users-menu-item users-menu-item--warn"
                                    onClick={() => {
                                      setMenuFor(null);
                                      setConfirm({ kind: "block", user: u });
                                    }}
                                  >
                                    Заблокировать
                                  </button>
                                )}
                                {!isSelf && (
                                  <button
                                    className="users-menu-item users-menu-item--danger"
                                    onClick={() => {
                                      setMenuFor(null);
                                      setConfirm({ kind: "delete", user: u });
                                    }}
                                  >
                                    Удалить пользователя
                                  </button>
                                )}
                              </div>
                            )}
                          </div>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}

          <div className="users-footer">
            {loadingMore && <span className="users-count" style={{ opacity: 0.7 }}>Загружаем ещё…</span>}
            {hasMore && !loadingMore && (
              <button className="admin__back" onClick={() => void loadPage(Math.ceil(users.length / PAGE_SIZE) + 1, appliedSearch, false)}>
                Загрузить ещё
              </button>
            )}
            {!hasMore && users.length > 0 && (
              <span className="users-count" style={{ opacity: 0.7 }}>
                Показаны все
              </span>
            )}
          </div>
        </>
      )}

      {confirm && (
        <div className="modal-backdrop" onClick={() => setConfirm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">
              {confirm.kind === "block" ? "Заблокировать пользователя" : "Удалить пользователя"}
            </div>
            <div className="modal__body">
              {confirm.kind === "block" ? (
                <>
                  Заблокировать <strong>{confirm.user.email}</strong>? Пользователь потеряет доступ к
                  аккаунту (данные сохранятся и могут быть восстановлены).
                </>
              ) : (
                <>
                  Удалить <strong>{confirm.user.email}</strong>? Аккаунт будет скрыт из системы
                  (мягкое удаление) — документы и чаты останутся в базе.
                </>
              )}
            </div>
            <div className="modal__actions">
              <button className="admin__back" onClick={() => setConfirm(null)}>
                Отмена
              </button>
              <button
                className="modal__btn modal__btn--danger"
                onClick={() =>
                  confirm.kind === "block" ? void handleBlockUnblock(confirm.user) : void handleDelete(confirm.user)
                }
              >
                {confirm.kind === "block" ? "Заблокировать" : "Удалить"}
              </button>
            </div>
          </div>
        </div>
      )}

      {reports && (
        <div className="modal-backdrop" onClick={() => setReports(null)}>
          <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">
              Жалобы на пользователя {reports.user.email}{" "}
              <span className="users-count">
                ({reports.total.toLocaleString("ru-RU")})
              </span>
            </div>
            {reports.error && <div className="admin__error">{reports.error}</div>}
            {reports.loading && <div className="admin__loading">Загружаем жалобы…</div>}
            {!reports.loading && reports.reports.length === 0 && (
              <div className="admin__loading">Жалоб нет.</div>
            )}
            {!reports.loading && reports.reports.length > 0 && (
              <div className="users-scroll">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>Кто пожаловался</th>
                      <th>Причина</th>
                      <th>Описание</th>
                      <th>Дата</th>
                      <th>Статус</th>
                      <th>Решение</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.reports.map((r) => (
                      <tr key={r.id}>
                        <td className="admin-table__email">{r.reporter_email}</td>
                        <td>{r.reason}</td>
                        <td className="admin-table__desc">{r.description ?? "—"}</td>
                        <td className="admin-table__muted">{formatDate(r.created_at)}</td>
                        <td>
                          <span className={`pill pill--report pill--report-${r.status}`}>
                            {STATUS_LABELS[r.status]}
                          </span>
                        </td>
                        <td className="admin-table__muted">
                          {r.resolved_by_email
                            ? `${r.resolved_by_email} · ${formatDate(r.resolved_at)}`
                            : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            <div className="modal__actions">
              {reports.reports.length < reports.total && (
                <button className="admin__back" onClick={() => void loadMoreReports()} disabled={reports.loading}>
                  Загрузить ещё ({reports.reports.length} из {reports.total})
                </button>
              )}
              <button className="admin__back" onClick={() => setReports(null)}>
                Закрыть
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}