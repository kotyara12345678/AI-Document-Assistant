import { useCallback, useEffect, useRef, useState } from "react";
import { changePassword, deleteMe, fetchUsageStats, updateProfileAvatar } from "../api";
import type { UsageStats, UserOut } from "../types";
import { useI18n } from "../i18n";
import LanguageSwitcher from "./LanguageSwitcher";

const MAX_AVATAR_BYTES = 1_000_000;

interface ProfilePanelProps {
  user: UserOut;
  onBack: () => void;
  onUserUpdated: (user: UserOut) => void;
  theme: string;
  onToggleTheme: () => void;
  onLogout: () => void;
  onDeleted?: () => void;
}

export default function ProfilePanel({ user, onBack, onUserUpdated, theme, onToggleTheme, onLogout, onDeleted }: ProfilePanelProps) {
  const { t, formatNumber } = useI18n();
  const [avatar, setAvatar] = useState<string | null>(user.avatar_url ?? null);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarError, setAvatarError] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [newPasswordConfirm, setNewPasswordConfirm] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [passwordMsg, setPasswordMsg] = useState<string | null>(null);
  const [passwordError, setPasswordError] = useState<string | null>(null);

  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  const [stats, setStats] = useState<UsageStats | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    fetchUsageStats()
      .then((s) => {
        if (!cancelled) setStats(s);
      })
      .catch(() => {
        /* stats are non-critical */
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const onFileChosen = useCallback(
    (file: File | undefined) => {
      setAvatarError(null);
      if (!file) return;
      if (!file.type.startsWith("image/")) {
        setAvatarError(t("profile.avatarTypeError"));
        return;
      }
      if (file.size > MAX_AVATAR_BYTES) {
        setAvatarError(t("profile.avatarSizeError"));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => {
        const result = typeof reader.result === "string" ? reader.result : null;
        setAvatar(result);
        if (result) {
          setAvatarBusy(true);
          updateProfileAvatar(result)
            .then(onUserUpdated)
            .catch((err: unknown) => {
              setAvatarError(err instanceof Error ? err.message : t("profile.avatarSaveFail"));
            })
            .finally(() => setAvatarBusy(false));
        }
      };
      reader.readAsDataURL(file);
    },
    [onUserUpdated, t]
  );

  const removeAvatar = useCallback(async () => {
    setAvatarError(null);
    setAvatar(null);
    setAvatarBusy(true);
    try {
      const updated = await updateProfileAvatar(null);
      onUserUpdated(updated);
    } catch (err) {
      setAvatarError(err instanceof Error ? err.message : t("profile.avatarRemoveFail"));
    } finally {
      setAvatarBusy(false);
    }
  }, [onUserUpdated, t]);

  const submitPassword = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setPasswordMsg(null);
      setPasswordError(null);
      if (newPassword.length < 6) {
        setPasswordError(t("profile.pwdTooShort"));
        return;
      }
      if (newPassword !== newPasswordConfirm) {
        setPasswordError(t("profile.pwdMismatch"));
        return;
      }
      setPasswordBusy(true);
      try {
        const updated = await changePassword(currentPassword, newPassword, newPasswordConfirm);
        onUserUpdated(updated);
        setCurrentPassword("");
        setNewPassword("");
        setNewPasswordConfirm("");
        setPasswordMsg(t("profile.pwdChanged"));
      } catch (err) {
        setPasswordError(err instanceof Error ? err.message : t("profile.pwdChangeFail"));
      } finally {
        setPasswordBusy(false);
      }
    },
    [currentPassword, newPassword, newPasswordConfirm, onUserUpdated, t]
  );

  const confirmDelete = useCallback(() => {
    const ok = window.confirm(t("profile.deleteConfirm"));
    if (!ok) return;
    setDeleteError(null);
    setDeleting(true);
    deleteMe()
      .then(onDeleted)
      .catch((err: unknown) => {
        setDeleting(false);
        setDeleteError(err instanceof Error ? err.message : t("profile.deleteFail"));
      });
  }, [onDeleted, t]);

  return (
    <div className="profile-page">
      <header className="profile-page__header">
        <button className="profile-page__back" onClick={onBack}>
          {t("profile.backToChat")}
        </button>
        <h1 className="profile-page__title">{t("profile.title")}</h1>
        <div className="profile-page__spacer" />
      </header>

      <div className="profile-page__body">
        <section className="profile-card">
          <h2 className="profile-card__title">{t("profile.avatarTitle")}</h2>
          <div className="profile-avatar-row">
            {avatar ? (
              <img className="profile-avatar profile-avatar--lg" src={avatar} alt={t("profile.avatarAlt")} />
            ) : (
              <span className="profile-avatar profile-avatar--lg profile-avatar--fallback">
                {user.email.slice(0, 1).toUpperCase()}
              </span>
            )}
            <div className="profile-avatar-actions">
              <button className="modal__btn" onClick={() => fileRef.current?.click()} disabled={avatarBusy}>
                {avatarBusy ? t("profile.saving") : t("profile.uploadPhoto")}
              </button>
              {avatar && (
                <button className="modal__btn" onClick={() => void removeAvatar()} disabled={avatarBusy}>
                  {t("profile.remove")}
                </button>
              )}
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => onFileChosen(e.target.files?.[0])}
              />
            </div>
          </div>
          {avatarError && <div className="profile-error">{avatarError}</div>}
        </section>

        <section className="profile-card">
          <h2 className="profile-card__title">{t("profile.passwordTitle")}</h2>
          <form className="profile-form" onSubmit={(e) => void submitPassword(e)}>
            <input
              className="profile-input"
              type="password"
              placeholder={t("profile.currentPasswordPh")}
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              autoComplete="current-password"
            />
            <input
              className="profile-input"
              type="password"
              placeholder={t("profile.newPasswordPh")}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              autoComplete="new-password"
            />
            <input
              className="profile-input"
              type="password"
              placeholder={t("profile.repeatPasswordPh")}
              value={newPasswordConfirm}
              onChange={(e) => setNewPasswordConfirm(e.target.value)}
              autoComplete="new-password"
            />
            {passwordError && <div className="profile-error">{passwordError}</div>}
            {passwordMsg && <div className="profile-ok">{passwordMsg}</div>}
            <button className="modal__btn profile-form__submit" type="submit" disabled={passwordBusy || !currentPassword}>
              {passwordBusy ? t("profile.saving") : t("profile.changePasswordBtn")}
            </button>
          </form>
        </section>

        <section className="profile-card">
          <h2 className="profile-card__title">{t("profile.tokensTitle")}</h2>
          {stats ? (
            <div className="profile-stats">
              <div className="profile-stats__row">
                <span>{t("profile.statTotal")}</span>
                <b>{formatNumber(stats.total_tokens)}</b>
              </div>
              <div className="profile-stats__row">
                <span>{t("profile.statToday")}</span>
                <b>{formatNumber(stats.tokens_today)}</b>
              </div>
              <div className="profile-stats__row">
                <span>{t("profile.stat7d")}</span>
                <b>{formatNumber(stats.tokens_7d)}</b>
              </div>
              <div className="profile-stats__row">
                <span>{t("profile.stat30d")}</span>
                <b>{formatNumber(stats.tokens_30d)}</b>
              </div>
              <div className="profile-stats__row">
                <span>{t("profile.statRequests")}</span>
                <b>{formatNumber(stats.requests)}</b>
              </div>
            </div>
          ) : (
            <div className="profile-stats__empty">{t("profile.statsLoading")}</div>
          )}
        </section>

        <section className="profile-card">
          <h2 className="profile-card__title">{t("profile.themeTitle")}</h2>
          <div className="profile-settings-row">
            <span>{t("profile.themeLabel")}</span>
            <button className="profile-theme-btn" onClick={onToggleTheme}>
              {theme === "dark" ? t("profile.themeLight") : t("profile.themeDark")}
            </button>
          </div>
          <div className="profile-settings-row">
            <span>{t("lang.title")}</span>
            <LanguageSwitcher />
          </div>
          <button className="profile-logout-btn" onClick={onLogout}>
            <svg className="profile-logout-icon" width="20" height="20" viewBox="0 0 24 24" fill="currentColor" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
              <path d="M16.8 2H14.2C11 2 9 4 9 7.2V11.25H15.25C15.66 11.25 16 11.59 16 12C16 12.41 15.66 12.75 15.25 12.75H9V16.8C9 20 11 22 14.2 22H16.79C19.99 22 21.99 20 21.99 16.8V7.2C22 4 20 2 16.8 2Z" />
              <path d="M4.55994 11.2498L6.62994 9.17984C6.77994 9.02984 6.84994 8.83984 6.84994 8.64984C6.84994 8.45984 6.77994 8.25984 6.62994 8.11984C6.33994 7.82984 5.85994 7.82984 5.56994 8.11984L2.21994 11.4698C1.92994 11.7598 1.92994 12.2398 2.21994 12.5298L5.56994 15.8798C5.85994 16.1698 6.33994 16.1698 6.62994 15.8798C6.91994 15.5898 6.91994 15.1098 6.62994 14.8198L4.55994 12.7498H8.99994V11.2498H4.55994Z" />
            </svg>
            {t("profile.logout")}
          </button>
        </section>

        {onDeleted && (
          <section className="profile-card profile-card--danger">
            <h2 className="profile-card__title">{t("profile.dangerTitle")}</h2>
            <p className="profile-danger-text">{t("profile.dangerText")}</p>
            {deleteError && <div className="profile-error">{deleteError}</div>}
            <button
              type="button"
              className="profile-delete-btn"
              onClick={confirmDelete}
              disabled={deleting}
            >
              {deleting ? t("profile.deleting") : t("profile.deleteAccount")}
            </button>
          </section>
        )}
      </div>
    </div>
  );
}
