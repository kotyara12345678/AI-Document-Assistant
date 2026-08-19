import { useState } from "react";
import type { FormEvent } from "react";
import type { UserOut } from "../types";
import { login, register, setToken } from "../api";
import { useI18n } from "../i18n";
import LanguageSwitcher from "./LanguageSwitcher";

type Mode = "login" | "register";

interface Props {
  onAuthed: (user: UserOut) => void;
  initialMode?: Mode;
  onBack?: () => void;
  onOpenPrivacy?: () => void;
  onOpenCookies?: () => void;
}

export default function AuthScreen({ onAuthed, initialMode, onBack, onOpenPrivacy, onOpenCookies }: Props) {
  const { t } = useI18n();
  const [mode, setMode] = useState<Mode>(initialMode ?? "login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
  const [consent, setConsent] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const switchMode = (next: Mode) => {
    if (next === mode) return;
    setMode(next);
    setError(null);
    setPassword("");
    setPasswordConfirm("");
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (busy) return;
    setError(null);

    if (mode === "register" && password !== passwordConfirm) {
      setError(t("auth.passwordsMismatch"));
      return;
    }

    if (mode === "register" && !consent) {
      setError(t("auth.consentRequired"));
      return;
    }

    setBusy(true);
    try {
      const data = mode === "register" ? await register(email, password, passwordConfirm) : await login(email, password);
      setToken(data.access_token);
      onAuthed(data.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : t("auth.genericError"));
    } finally {
      setBusy(false);
    }
  };

  const isRegister = mode === "register";

  return (
    <div className="auth">
      <form className="auth__card" onSubmit={submit}>
        <div className="auth__top-row">
          {onBack && (
            <button type="button" className="auth__back" onClick={onBack}>
              {t("auth.backToSite")}
            </button>
          )}
          <LanguageSwitcher className="auth__lang" />
        </div>
        <div className="auth__brand">ADA</div>
        <p className="auth__subtitle">
          {isRegister ? t("auth.registerSubtitle") : t("auth.loginSubtitle")}
        </p>

        <div className="auth__tabs">
          <button
            type="button"
            className={`auth__tab ${!isRegister ? "auth__tab--active" : ""}`}
            onClick={() => switchMode("login")}
          >
            {t("auth.login")}
          </button>
          <button
            type="button"
            className={`auth__tab ${isRegister ? "auth__tab--active" : ""}`}
            onClick={() => switchMode("register")}
          >
            {t("auth.register")}
          </button>
        </div>

        <div className="auth__form">
          <label className="auth__field">
            <span className="auth__label">{t("auth.email")}</span>
            <input
              className="auth__input"
              type="email"
              required
              autoComplete="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
            />
          </label>

          <div className="auth__field">
            <span className="auth__label">{t("auth.password")}</span>
            <input
              className="auth__input"
              type={showPassword ? "text" : "password"}
              required
              minLength={isRegister ? 8 : 1}
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isRegister ? t("auth.passwordHint") : t("auth.enterPassword")}
            />
            <div className="auth__field-actions">
              <span />
              <button
                type="button"
                className="auth__show-password"
                onClick={() => setShowPassword((v) => !v)}
                title={showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
              >
                {showPassword ? t("auth.hidePassword") : t("auth.showPassword")}
              </button>
            </div>
          </div>

          {isRegister && (
            <label className="auth__field">
              <span className="auth__label">{t("auth.repeatPassword")}</span>
              <input
                className="auth__input"
                type={showPassword ? "text" : "password"}
                required
                minLength={8}
                autoComplete="new-password"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                placeholder={t("auth.repeatPasswordPlaceholder")}
              />
            </label>
          )}

          {isRegister && (
            <label className="auth__consent">
              <input
                className="auth__consent-box"
                type="checkbox"
                checked={consent}
                onChange={(e) => setConsent(e.target.checked)}
              />
              <span className="auth__consent-text">
                {t("auth.consentText")}{" "}
                <button
                  type="button"
                  className="auth__link"
                  onClick={(e) => {
                    e.preventDefault();
                    onOpenPrivacy?.();
                  }}
                >
                  {t("auth.privacyPolicyLabel")}
                </button>
                .
              </span>
            </label>
          )}

          {error && <div className="auth__error">{error}</div>}

          <button className="auth__submit" type="submit" disabled={busy}>
            {busy ? (
              <span className="auth__spinner" />
            ) : isRegister ? (
              t("auth.createAccount")
            ) : (
              t("auth.loginAction")
            )}
          </button>

          <p className="auth__switch-hint">
            {isRegister ? t("auth.haveAccount") : t("auth.noAccount")}{" "}
            <button
              type="button"
              className="auth__link"
              onClick={() => switchMode(isRegister ? "login" : "register")}
            >
              {isRegister ? t("auth.loginAction") : t("auth.registerLink")}
            </button>
          </p>

          <p className="auth__cookie-note">
            {t("auth.cookieNote")}{" "}
            <button type="button" className="auth__link" onClick={() => onOpenCookies?.()}>
              Cookie Policy
            </button>
          </p>
        </div>
      </form>
    </div>
  );
}
