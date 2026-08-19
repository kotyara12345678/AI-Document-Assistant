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
import { useI18n } from "../i18n";

const PAGE_SIZE = 20;

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

export default function UsersAdmin({ currentUserId }: Props) {
  const { t, formatNumber, formatDate } = useI18n();
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

  const roleLabel = (role: UserRole): string => {
    switch (role) {
      case "admin":
        return t("users.roleAdmin");
      case "moderator":
        return t("users.roleModerator");
      default:
        return t("users.roleUser");
    }
  };

  const statusLabel = (status: AdminReportStatus): string => {
    switch (status) {
      case "pending":
        return t("users.statusPending");
      case "reviewed":
        return t("users.statusReviewed");
      case "rejected":
        return t("users.statusRejected");
      default:
        return t("users.statusActionTaken");
    }
  };

  const formatDateTime = (value: string | null): string => {
    if (!value) return "—";
    return formatDate(value, {
      day: "2-digit",
      month: "2-digit",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  };

  useEffect(() => {
    const timer = window.setTimeout(() => setAppliedSearch(search.trim()), 400);
    return () => window.clearTimeout(timer);
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
        setError(err instanceof Error ? err.message : t("users.errorLoad"));
      } finally {
        setLoading(false);
        setLoadingMore(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void loadPage(1, appliedSearch, true);
  }, [appliedSearch, loadPage]);

  const replaceRow = useCallback((updated: AdminUser) => {
    setUsers((prev) => prev.map((u) => (u.id === updated.id ? updated : u)));
  }, []);

  const applyError = useCallback(
    (err: unknown): string => {
      return err instanceof Error ? err.message : t("users.errorOperation");
    },
    [t],
  );

  const handleRole = useCallback(
    async (user: AdminUser, role: UserRole) => {
      setMenuFor(null);
      setError(null);
      try {
        const updated = await patchAdminUserRole(user.id, role);
        replaceRow(updated);
        flashNotice(t("users.noticeRoleChanged", { email: user.email, role: roleLabel(role) }));
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice, replaceRow, t],
  );

  const handleBlockUnblock = useCallback(
    async (user: AdminUser) => {
      setConfirm(null);
      setMenuFor(null);
      setError(null);
      try {
        const updated = await patchAdminUserStatus(user.id, !user.is_active);
        replaceRow(updated);
        flashNotice(updated.is_active ? t("users.noticeUnblocked") : t("users.noticeBlocked"));
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice, replaceRow, t],
  );

  const handleDelete = useCallback(
    async (user: AdminUser) => {
      setConfirm(null);
      setMenuFor(null);
      setError(null);
      try {
        await deleteAdminUser(user.id);
        setUsers((prev) => prev.filter((u) => u.id !== user.id));
        setTotal((n) => Math.max(0, n - 1));
        flashNotice(t("users.noticeDeleted", { email: user.email }));
      } catch (err) {
        setError(applyError(err));
      }
    },
    [applyError, flashNotice, t],
  );

  const openReports = useCallback(
    async (user: AdminUser) => {
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
          error: err instanceof Error ? err.message : t("users.errorReports"),
        });
      }
    },
    [t],
  );

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
          placeholder={t("users.searchPh")}
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <span className="users-count">
          {formatNumber(total)} {t("users.count", { count: total })}
        </span>
      </div>

      {error && <div className="admin__error">{error}</div>}
      {notice && <div className="admin__notice">{notice}</div>}
      {loading && <div className="admin__loading">{t("users.loading")}</div>}

      {!loading && (
        <>
          {users.length === 0 ? (
            <div className="admin__loading">{t("users.notFound")}</div>
          ) : (
            <div className="users-scroll">
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>{t("users.thUser")}</th>
                    <th>{t("users.thEmail")}</th>
                    <th>{t("users.thRegistered")}</th>
                    <th>{t("users.thRole")}</th>
                    <th>{t("users.thReports")}</th>
                    <th>{t("users.thStatus")}</th>
                    <th>{t("users.thActivity")}</th>
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
                          {isSelf && <span className="users-self">{t("users.selfSuffix")}</span>}
                        </td>
                        <td className="admin-table__email">{u.email}</td>
                        <td className="admin-table__muted">{formatDateTime(u.created_at)}</td>
                        <td>
                          <span className={`pill pill--role pill--role-${u.role}`}>
                            {roleLabel(u.role)}
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
                            {u.is_active ? t("users.active") : t("users.blocked")}
                          </span>
                        </td>
                        <td className="admin-table__muted">{formatDateTime(u.last_active_at)}</td>
                        <td>
                          <div className="users-menu">
                            {menuFor === u.id && <div className="users-menu-backdrop" onClick={() => setMenuFor(null)} />}
                            <button
                              className="users-menu-btn"
                              onClick={() => setMenuFor((cur) => (cur === u.id ? null : u.id))}
                              aria-label={t("users.actionsAria")}
                              title={t("users.actionsAria")}
                            >
                              ⋮
                            </button>
                            {menuFor === u.id && (
                              <div className="users-menu-list">
                                <button className="users-menu-item" onClick={() => void openReports(u)}>
                                  {t("users.viewReports")}
                                </button>
                                {!isSelf && u.role !== "admin" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "admin")}>
                                    {t("users.makeAdmin")}
                                  </button>
                                )}
                                {!isSelf && u.role !== "moderator" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "moderator")}>
                                    {t("users.makeModerator")}
                                  </button>
                                )}
                                {!isSelf && u.role === "admin" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "user")}>
                                    {t("users.revokeAdmin")}
                                  </button>
                                )}
                                {!isSelf && u.role === "moderator" && (
                                  <button className="users-menu-item" onClick={() => void handleRole(u, "user")}>
                                    {t("users.revokeModerator")}
                                  </button>
                                )}
                                {!isSelf && !u.is_active && (
                                  <button className="users-menu-item" onClick={() => void handleBlockUnblock(u)}>
                                    {t("users.unblockAction")}
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
                                    {t("users.blockAction")}
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
                                    {t("users.deleteUser")}
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
            {loadingMore && <span className="users-count" style={{ opacity: 0.7 }}>{t("users.loadingMore")}</span>}
            {hasMore && !loadingMore && (
              <button className="admin__back" onClick={() => void loadPage(Math.ceil(users.length / PAGE_SIZE) + 1, appliedSearch, false)}>
                {t("users.loadMore")}
              </button>
            )}
            {!hasMore && users.length > 0 && (
              <span className="users-count" style={{ opacity: 0.7 }}>
                {t("users.allShown")}
              </span>
            )}
          </div>
        </>
      )}

      {confirm && (
        <div className="modal-backdrop" onClick={() => setConfirm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">
              {confirm.kind === "block" ? t("users.modalBlockTitle") : t("users.modalDeleteTitle")}
            </div>
            <div className="modal__body">
              {t(confirm.kind === "block" ? "users.modalBlockBody" : "users.modalDeleteBody", {
                email: confirm.user.email,
              })}
            </div>
            <div className="modal__actions">
              <button className="admin__back" onClick={() => setConfirm(null)}>
                {t("users.cancel")}
              </button>
              <button
                className="modal__btn modal__btn--danger"
                onClick={() =>
                  confirm.kind === "block" ? void handleBlockUnblock(confirm.user) : void handleDelete(confirm.user)
                }
              >
                {confirm.kind === "block" ? t("users.blockBtn") : t("users.deleteBtn")}
              </button>
            </div>
          </div>
        </div>
      )}

      {reports && (
        <div className="modal-backdrop" onClick={() => setReports(null)}>
          <div className="modal modal--wide" onClick={(e) => e.stopPropagation()}>
            <div className="modal__title">
              {t("users.reportsTitle", { email: reports.user.email })}{" "}
              <span className="users-count">({formatNumber(reports.total)})</span>
            </div>
            {reports.error && <div className="admin__error">{reports.error}</div>}
            {reports.loading && <div className="admin__loading">{t("users.loadingReports")}</div>}
            {!reports.loading && reports.reports.length === 0 && (
              <div className="admin__loading">{t("users.noReports")}</div>
            )}
            {!reports.loading && reports.reports.length > 0 && (
              <div className="users-scroll">
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>{t("users.thReporter")}</th>
                      <th>{t("users.thReason")}</th>
                      <th>{t("users.thDescription")}</th>
                      <th>{t("users.thDate")}</th>
                      <th>{t("users.thReportStatus")}</th>
                      <th>{t("users.thResolution")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {reports.reports.map((r) => (
                      <tr key={r.id}>
                        <td className="admin-table__email">{r.reporter_email}</td>
                        <td>{r.reason}</td>
                        <td className="admin-table__desc">{r.description ?? "—"}</td>
                        <td className="admin-table__muted">{formatDateTime(r.created_at)}</td>
                        <td>
                          <span className={`pill pill--report pill--report-${r.status}`}>
                            {statusLabel(r.status)}
                          </span>
                        </td>
                        <td className="admin-table__muted">
                          {r.resolved_by_email
                            ? `${r.resolved_by_email} · ${formatDateTime(r.resolved_at)}`
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
                  {t("users.loadMoreOf", { loaded: reports.reports.length, total: reports.total })}
                </button>
              )}
              <button className="admin__back" onClick={() => setReports(null)}>
                {t("users.close")}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
