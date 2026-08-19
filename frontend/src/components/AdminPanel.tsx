import { useEffect, useState } from "react";
import { fetchAdminStats } from "../api";
import type { AdminStats } from "../types";
import { useI18n } from "../i18n";
import UsersAdmin from "./UsersAdmin";

interface Props {
  onBack: () => void;
  currentUserId: number;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat-card">
      <div className="stat-card__value">{value}</div>
      <div className="stat-card__title">{label}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const ok = status === "ok";
  return (
    <span className={`pill ${ok ? "pill--ok" : "pill--down"}`}>
      {ok ? "✓" : "✗"} {status}
    </span>
  );
}

export default function AdminPanel({ onBack, currentUserId }: Props) {
  const { t, formatNumber, formatDate } = useI18n();
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<"stats" | "users">("stats");

  useEffect(() => {
    let cancelled = false;
    fetchAdminStats()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : t("admin.errorLoad"));
      });
    return () => {
      cancelled = true;
    };
  }, [t]);

  return (
    <main className="admin">
      <div className="admin__inner">
        <div className="admin__header">
          <button className="admin__back" onClick={onBack}>
            {t("admin.back")}
          </button>
          <div>
            <h1 className="admin__title">{t("admin.title")}</h1>
            <div className="admin__subtitle">{t("admin.subtitle")}</div>
          </div>
        </div>

        <div className="admin__tabs">
          <button
            className={`admin__tab ${view === "stats" ? "admin__tab--active" : ""}`}
            onClick={() => setView("stats")}
          >
            {t("admin.tabStats")}
          </button>
          <button
            className={`admin__tab ${view === "users" ? "admin__tab--active" : ""}`}
            onClick={() => setView("users")}
          >
            {t("admin.tabUsers")}
          </button>
        </div>

        {view === "users" ? (
          <UsersAdmin currentUserId={currentUserId} />
        ) : (
          <>
        {error && (
          <div className="admin__error">
            {error} {t("admin.errorAccess")}
          </div>
        )}
        {!error && !stats && <div className="admin__loading">{t("admin.loading")}</div>}
        {!error && stats && (
          <div className="admin__body">
            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secServices")}</h2>
              <div className="admin-row">
                <StatusPill status={stats.services.database} />
                <span className="admin-row-label">{t("admin.labelDatabase")}</span>
                <StatusPill status={stats.services.qdrant} />
                <span className="admin-row-label">{t("admin.labelVectors")}</span>
                <StatusPill status={stats.services.status} />
                <span className="admin-row-label">{t("admin.labelOverall")}</span>
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secUsers")}</h2>
              <div className="admin__stats-grid">
                <StatCard label={t("admin.labelTotal")} value={stats.users.total} />
                <StatCard label={t("admin.labelAdmins")} value={stats.users.admins} />
                <StatCard label={t("admin.labelNew24h")} value={stats.users.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secDocuments")}</h2>
              <div className="admin__stats-grid">
                <StatCard label={t("admin.labelDocs")} value={stats.documents.total} />
                <StatCard label={t("admin.labelChunks")} value={stats.documents.chunks} />
                <StatCard
                  label={t("admin.labelContentChars")}
                  value={formatNumber(stats.documents.total_content_chars)}
                />
                <StatCard label={t("admin.labelDocsNew24h")} value={stats.documents.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secChats")}</h2>
              <div className="admin__stats-grid">
                <StatCard label={t("admin.labelDialogs")} value={stats.chats.total} />
                <StatCard label={t("admin.labelMessages")} value={stats.chats.messages} />
                <StatCard label={t("admin.labelChatsNew24h")} value={stats.chats.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secRequests")}</h2>
              <div className="admin__stats-grid">
                <StatCard label={t("admin.labelApiRequests")} value={stats.requests.api_total} />
                <StatCard label={t("admin.labelLlmRequests")} value={stats.requests.llm_requests} />
                <StatCard label={t("admin.labelLatency")} value={stats.requests.average_latency_ms} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">{t("admin.secTokens")}</h2>
              <div className="admin__stats-grid">
                <StatCard label={t("admin.labelTokensUsed")} value={stats.tokens.total_tokens_used} />
                <StatCard label={t("admin.labelHttpErrors")} value={stats.errors.total} />
                <StatCard label="4xx" value={stats.errors.status_buckets["4xx"] ?? 0} />
                <StatCard label="5xx" value={stats.errors.status_buckets["5xx"] ?? 0} />
              </div>
            </section>

            {stats.errors.recent.length > 0 && (
              <section className="admin__section">
                <h2 className="admin__section-title">{t("admin.secRecentErrors")}</h2>
                <table className="admin__errors-table">
                  <thead>
                    <tr>
                      <th>{t("admin.thTime")}</th>
                      <th>{t("admin.thStatus")}</th>
                      <th>{t("admin.thPath")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.errors.recent.map((e, i) => (
                      <tr key={i}>
                        <td>{formatDate(e.timestamp, { dateStyle: "short", timeStyle: "short" })}</td>
                        <td>
                          <span className="admin__errors-count">{e.status}</span>
                        </td>
                        <td className="admin__errors-path">{e.path}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            )}
          </div>
        )}
          </>
        )}
      </div>
    </main>
  );
}
