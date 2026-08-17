import { useEffect, useState } from "react";
import { fetchAdminStats } from "../api";
import type { AdminStats } from "../types";
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
        if (!cancelled) setError(err instanceof Error ? err.message : "Не удалось загрузить статистику");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <main className="admin">
      <div className="admin__inner">
        <div className="admin__header">
          <button className="admin__back" onClick={onBack}>
            ← К чату
          </button>
          <div>
            <h1 className="admin__title">Панель администратора</h1>
            <div className="admin__subtitle">
              Сводная статистика платформы — содержимое документов и чатов здесь не отображается.
            </div>
          </div>
        </div>

        <div className="admin__tabs">
          <button
            className={`admin__tab ${view === "stats" ? "admin__tab--active" : ""}`}
            onClick={() => setView("stats")}
          >
            Статистика
          </button>
          <button
            className={`admin__tab ${view === "users" ? "admin__tab--active" : ""}`}
            onClick={() => setView("users")}
          >
            Пользователи
          </button>
        </div>

        {view === "users" ? (
          <UsersAdmin currentUserId={currentUserId} />
        ) : (
          <>
        {error && (
          <div className="admin__error">
            {error} — бэкенд отклонил доступ администратора (HTTP 401/403).
          </div>
        )}
        {!error && !stats && <div className="admin__loading">Загружаем статистику…</div>}
        {!error && stats && (
          <div className="admin__body">
            <section className="admin__section">
              <h2 className="admin__section-title">Сервисы</h2>
              <div className="admin-row">
                <StatusPill status={stats.services.database} />
                <span className="admin-row-label">база данных</span>
                <StatusPill status={stats.services.qdrant} />
                <span className="admin-row-label">векторный поиск</span>
                <StatusPill status={stats.services.status} />
                <span className="admin-row-label">общее состояние</span>
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Пользователи</h2>
              <div className="admin__stats-grid">
                <StatCard label="Всего" value={stats.users.total} />
                <StatCard label="Администраторы" value={stats.users.admins} />
                <StatCard label="Новых за 24 ч" value={stats.users.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Документы</h2>
              <div className="admin__stats-grid">
                <StatCard label="Документы" value={stats.documents.total} />
                <StatCard label="Фрагментов в индексе" value={stats.documents.chunks} />
                <StatCard
                  label="Символов в содержимом"
                  value={stats.documents.total_content_chars.toLocaleString("ru-RU")}
                />
                <StatCard label="Загружено за 24 ч" value={stats.documents.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Чаты</h2>
              <div className="admin__stats-grid">
                <StatCard label="Диалоги" value={stats.chats.total} />
                <StatCard label="Сообщения" value={stats.chats.messages} />
                <StatCard label="Новых за 24 ч" value={stats.chats.new_last_24h} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Запросы</h2>
              <div className="admin__stats-grid">
                <StatCard label="API-запросы" value={stats.requests.api_total} />
                <StatCard label="LLM-запросы" value={stats.requests.llm_requests} />
                <StatCard label="Средняя задержка (мс)" value={stats.requests.average_latency_ms} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Токены и ошибки</h2>
              <div className="admin__stats-grid">
                <StatCard label="Использовано токенов" value={stats.tokens.total_tokens_used} />
                <StatCard label="HTTP-ошибки" value={stats.errors.total} />
                <StatCard label="4xx" value={stats.errors.status_buckets["4xx"] ?? 0} />
                <StatCard label="5xx" value={stats.errors.status_buckets["5xx"] ?? 0} />
              </div>
            </section>

            {stats.errors.recent.length > 0 && (
              <section className="admin__section">
                <h2 className="admin__section-title">Последние ошибки (только пути)</h2>
                <table className="admin__errors-table">
                  <thead>
                    <tr>
                      <th>Время</th>
                      <th>Статус</th>
                      <th>Путь</th>
                    </tr>
                  </thead>
                  <tbody>
                    {stats.errors.recent.map((e, i) => (
                      <tr key={i}>
                        <td>{new Date(e.timestamp).toLocaleString("ru-RU")}</td>
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
