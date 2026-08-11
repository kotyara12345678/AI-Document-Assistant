import { useEffect, useState } from "react";
import { fetchAdminStats } from "../api";
import type { AdminStats } from "../types";

interface Props {
  onBack: () => void;
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="admin-card">
      <div className="admin-card__value">{value}</div>
      <div className="admin-card__label">{label}</div>
    </div>
  );
}

function StatusPill({ status }: { status: string }) {
  const ok = status === "ok";
  return (
    <span className={`admin-pill ${ok ? "admin-pill--ok" : "admin-pill--bad"}`}>
      {ok ? "✓" : "✗"} {status}
    </span>
  );
}

export default function AdminPanel({ onBack }: Props) {
  const [stats, setStats] = useState<AdminStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAdminStats()
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load stats");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="admin">
      <div className="admin__header">
        <button className="btn" onClick={onBack}>
          ← Back to chat
        </button>
        <div>
          <h1 className="admin__title">Admin Panel</h1>
          <div className="admin__subtitle">Aggregated platform statistics — no document or chat content is shown here.</div>
        </div>
      </div>

      {error && (
        <div className="banner banner--error">
          {error} — the backend rejected your admin access (HTTP 401/403).
        </div>
      )}
      {!error && !stats && <div className="admin__loading">Loading statistics…</div>}
      {!error && stats && (
        <div className="admin__body">
          <section className="admin-section">
            <h2 className="admin-section__title">Services</h2>
            <div className="admin-row">
              <StatusPill status={stats.services.database} />
              <span>database</span>
              <StatusPill status={stats.services.qdrant} />
              <span>vector search</span>
              <StatusPill status={stats.services.status} />
              <span>overall</span>
            </div>
          </section>

          <section className="admin-section">
            <h2 className="admin-section__title">Users</h2>
            <div className="admin-grid">
              <StatCard label="Total users" value={stats.users.total} />
              <StatCard label="Admins" value={stats.users.admins} />
              <StatCard label="New (24h)" value={stats.users.new_last_24h} />
            </div>
          </section>

          <section className="admin-section">
            <h2 className="admin-section__title">Documents</h2>
            <div className="admin-grid">
              <StatCard label="Documents" value={stats.documents.total} />
              <StatCard label="Index chunks" value={stats.documents.chunks} />
              <StatCard label="Content chars" value={stats.documents.total_content_chars.toLocaleString()} />
              <StatCard label="Uploaded (24h)" value={stats.documents.new_last_24h} />
            </div>
          </section>

          <section className="admin-section">
            <h2 className="admin-section__title">Chats</h2>
            <div className="admin-grid">
              <StatCard label="Conversations" value={stats.chats.total} />
              <StatCard label="Messages" value={stats.chats.messages} />
              <StatCard label="New (24h)" value={stats.chats.new_last_24h} />
            </div>
          </section>

          <section className="admin-section">
            <h2 className="admin-section__title">Requests</h2>
            <div className="admin-grid">
              <StatCard label="API requests" value={stats.requests.api_total} />
              <StatCard label="LLM prompts" value={stats.requests.llm_requests} />
              <StatCard label="Avg latency (ms)" value={stats.requests.average_latency_ms} />
            </div>
          </section>

          <section className="admin-section">
            <h2 className="admin-section__title">Tokens &amp; errors</h2>
            <div className="admin-grid">
              <StatCard label="Tokens used" value={stats.tokens.total_tokens_used} />
              <StatCard label="HTTP errors" value={stats.errors.total} />
              <StatCard label="4xx" value={stats.errors.status_buckets["4xx"] ?? 0} />
              <StatCard label="5xx" value={stats.errors.status_buckets["5xx"] ?? 0} />
            </div>
          </section>

          {stats.errors.recent.length > 0 && (
            <section className="admin-section">
              <h2 className="admin-section__title">Recent errors (paths only)</h2>
              <table className="admin-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>Status</th>
                    <th>Path</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.errors.recent.map((e, i) => (
                    <tr key={i}>
                      <td>{new Date(e.timestamp).toLocaleString()}</td>
                      <td>{e.status}</td>
                      <td>{e.path}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </section>
          )}
        </div>
      )}
    </main>
  );
}