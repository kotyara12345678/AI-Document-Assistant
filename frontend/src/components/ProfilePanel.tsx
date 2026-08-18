import { useEffect, useState } from "react";
import { changePassword, deleteMe, fetchMeStats } from "../api";
import type { MeStats, UserOut } from "../types";

interface Props {
  user: UserOut;
  onBack: () => void;
  onDeleted: () => void;
}

function roleLabel(role: string): string {
  switch (role) {
    case "admin":
      return "Администратор";
    case "moderator":
      return "Модератор";
    default:
      return "Пользователь";
  }
}

function formatDate(value: string | null): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString("ru-RU");
}

function StatCard({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="stat-card">
      <div className="stat-card__value">{value.toLocaleString("ru-RU")}</div>
      <div className="stat-card__title">{label}</div>
    </div>
  );
}

export default function ProfilePanel({ user, onBack, onDeleted }: Props) {
  const [stats, setStats] = useState<MeStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [changing, setChanging] = useState(false);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchMeStats()
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

  const submitPassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPasswordError(null);
    if (newPassword !== passwordConfirm) {
      setPasswordError("Пароли не совпадают");
      return;
    }
    setChanging(true);
    try {
      await changePassword(currentPassword, newPassword, passwordConfirm);
      setCurrentPassword("");
      setNewPassword("");
      setPasswordConfirm("");
      setNotice("Пароль изменён.");
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : "Не удалось изменить пароль");
    } finally {
      setChanging(false);
    }
  };

  const confirmDelete = () => {
    const ok = window.confirm(
      "Удалить аккаунт? Ваши документы, чаты и данные станут недоступны. Это действие нельзя отменить."
    );
    if (!ok) return;
    setDeleting(true);
    deleteMe()
      .then(onDeleted)
      .catch((err) => {
        setDeleting(false);
        setError(err instanceof Error ? err.message : "Не удалось удалить аккаунт");
      });
  };

  return (
    <main className="admin">
      <div className="admin__inner">
        <div className="admin__header">
          <button className="admin__back" onClick={onBack}>
            ← К чату
          </button>
          <div>
            <h1 className="admin__title">Личный кабинет</h1>
            <div className="admin__subtitle">
              Профиль и статистика использования — содержимое документов и чатов здесь не отображается.
            </div>
          </div>
        </div>

        {error && (
          <div className="admin__error">
            {error}
          </div>
        )}

        {notice && (
          <div className="admin__notice">
            {notice}
          </div>
        )}

        {!error && !stats && <div className="admin__loading">Загружаем данные…</div>}

        {!error && stats && (
          <div className="admin__body">
            <section className="admin__section">
              <h2 className="admin__section-title">Профиль</h2>
              <div className="profile-card">
                <span className="profile-card__avatar">{user.email.slice(0, 1).toUpperCase()}</span>
                <div className="profile-card__info">
                  <div className="profile-card__email">{stats.user.email}</div>
                  <div className="profile-card__meta">
                    {roleLabel(stats.user.role)} · зарегистрирован {formatDate(stats.user.created_at)}
                  </div>
                  <div className="profile-card__meta">
                    Последняя активность: {formatDate(stats.last_active_at)}
                  </div>
                </div>
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Использование</h2>
              <div className="admin__stats-grid">
                <StatCard label="Документы" value={stats.documents_total} />
                <StatCard label="Чаты" value={stats.chats_total} />
                <StatCard label="Сообщения" value={stats.messages_total} />
                <StatCard label="Использовано токенов" value={stats.tokens_used} />
              </div>
            </section>

            <section className="admin__section">
              <h2 className="admin__section-title">Безопасность</h2>
              <form className="profile-form" onSubmit={(e) => void submitPassword(e)}>
                <label className="profile-form__label">
                  Текущий пароль
                  <input
                    type="password"
                    className="profile-form__input"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    required
                    minLength={6}
                    autoComplete="current-password"
                  />
                </label>
                <label className="profile-form__label">
                  Новый пароль
                  <input
                    type="password"
                    className="profile-form__input"
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    minLength={6}
                    autoComplete="new-password"
                  />
                </label>
                <label className="profile-form__label">
                  Повторите новый пароль
                  <input
                    type="password"
                    className="profile-form__input"
                    value={passwordConfirm}
                    onChange={(e) => setPasswordConfirm(e.target.value)}
                    required
                    minLength={6}
                    autoComplete="new-password"
                  />
                </label>
                {passwordError && <div className="admin__error">{passwordError}</div>}
                <button
                  type="submit"
                  className="btn--admin"
                  disabled={changing || !currentPassword || !newPassword || !passwordConfirm}
                >
                  {changing ? "Сохраняем…" : "Сменить пароль"}
                </button>
              </form>
            </section>

            <section className="admin__section admin__section--danger">
              <h2 className="admin__section-title">Опасная зона</h2>
              <p className="admin__subtitle">
                Удаление аккаунта делает недоступными все ваши документы и чаты. Это действие нельзя отменить.
              </p>
              <button
                type="button"
                className="btn--danger"
                onClick={confirmDelete}
                disabled={deleting}
              >
                {deleting ? "Удаляем…" : "Удалить аккаунт"}
              </button>
            </section>
          </div>
        )}
      </div>
    </main>
  );
}