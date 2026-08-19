import { useState } from "react";
import type { FormEvent } from "react";
import type { UserOut } from "../types";
import { login, register, setToken } from "../api";

type Mode = "login" | "register";

interface Props {
  onAuthed: (user: UserOut) => void;
  initialMode?: Mode;
  onBack?: () => void;
  onOpenPrivacy?: () => void;
  onOpenCookies?: () => void;
}

export default function AuthScreen({ onAuthed, initialMode, onBack, onOpenPrivacy, onOpenCookies }: Props) {
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
      setError("Пароли не совпадают");
      return;
    }

    if (mode === "register" && !consent) {
      setError("Необходимо согласие на обработку персональных данных");
      return;
    }

    setBusy(true);
    try {
      const data = mode === "register" ? await register(email, password, passwordConfirm) : await login(email, password);
      setToken(data.access_token);
      onAuthed(data.user);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Произошла ошибка, попробуйте ещё раз");
    } finally {
      setBusy(false);
    }
  };

  const isRegister = mode === "register";

  return (
    <div className="auth">
      <form className="auth__card" onSubmit={submit}>
        {onBack && (
          <button type="button" className="auth__back" onClick={onBack}>
            ← На сайт
          </button>
        )}
        <div className="auth__brand">ADA</div>
        <p className="auth__subtitle">
          {isRegister
            ? "Создайте аккаунт, чтобы хранить и искать свои документы"
            : "Войдите, чтобы продолжить работу с документами"}
        </p>

        <div className="auth__tabs">
          <button
            type="button"
            className={`auth__tab ${!isRegister ? "auth__tab--active" : ""}`}
            onClick={() => switchMode("login")}
          >
            Вход
          </button>
          <button
            type="button"
            className={`auth__tab ${isRegister ? "auth__tab--active" : ""}`}
            onClick={() => switchMode("register")}
          >
            Регистрация
          </button>
        </div>

        <div className="auth__form">
          <label className="auth__field">
            <span className="auth__label">Электронная почта</span>
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
            <span className="auth__label">Пароль</span>
            <input
              className="auth__input"
              type={showPassword ? "text" : "password"}
              required
              minLength={isRegister ? 8 : 1}
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isRegister ? "Минимум 8 символов" : "Введите пароль"}
            />
            <div className="auth__field-actions">
              <span />
              <button
                type="button"
                className="auth__show-password"
                onClick={() => setShowPassword((v) => !v)}
                title={showPassword ? "Скрыть пароль" : "Показать пароль"}
              >
                {showPassword ? "Скрыть пароль" : "Показать пароль"}
              </button>
            </div>
          </div>

          {isRegister && (
            <label className="auth__field">
              <span className="auth__label">Повторите пароль</span>
              <input
                className="auth__input"
                type={showPassword ? "text" : "password"}
                required
                minLength={8}
                autoComplete="new-password"
                value={passwordConfirm}
                onChange={(e) => setPasswordConfirm(e.target.value)}
                placeholder="Ещё раз тот же пароль"
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
                Я согласен(на) на обработку моих персональных данных в соответствии с{" "}
                <button
                  type="button"
                  className="auth__link"
                  onClick={(e) => {
                    e.preventDefault();
                    onOpenPrivacy?.();
                  }}
                >
                  Политикой обработки персональных данных
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
              "Создать аккаунт"
            ) : (
              "Войти"
            )}
          </button>

          <p className="auth__switch-hint">
            {isRegister ? "Уже есть аккаунт?" : "Нет аккаунта?"}{" "}
            <button
              type="button"
              className="auth__link"
              onClick={() => switchMode(isRegister ? "login" : "register")}
            >
              {isRegister ? "Войти" : "Зарегистрироваться"}
            </button>
          </p>

          <p className="auth__cookie-note">
            Мы используем файлы cookie для работы и улучшения сервиса.{" "}
            <button type="button" className="auth__link" onClick={() => onOpenCookies?.()}>
              Cookie Policy
            </button>
          </p>
        </div>
      </form>
    </div>
  );
}
