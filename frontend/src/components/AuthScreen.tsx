import { useState } from "react";
import type { FormEvent } from "react";
import type { UserOut } from "../types";
import { login, register, setToken } from "../api";

type Mode = "login" | "register";

interface Props {
  onAuthed: (user: UserOut) => void;
}

export default function AuthScreen({ onAuthed }: Props) {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirm, setPasswordConfirm] = useState("");
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
      <div className="auth__bg">
        <span className="auth__blob auth__blob--1" />
        <span className="auth__blob auth__blob--2" />
        <span className="auth__blob auth__blob--3" />
        <div className="auth__grid" />
      </div>

      <form className="auth__card" onSubmit={submit}>
        <div className="auth__brand">
          <span className="auth__logo">📄</span>
          <span className="auth__title">
            Doc<span className="auth__title-accent">Search</span>
          </span>
        </div>
        <p className="auth__subtitle">
          {isRegister ? "Создайте аккаунт, чтобы хранить и искать свои документы" : "Войдите, чтобы продолжить работу с документами"}
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

        <label className="auth__field">
          <span className="auth__label">Пароль</span>
          <div className="auth__password">
            <input
              className="auth__input"
              type={showPassword ? "text" : "password"}
              required
              minLength={isRegister ? 6 : 1}
              autoComplete={isRegister ? "new-password" : "current-password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={isRegister ? "Минимум 6 символов" : "Введите пароль"}
            />
            <button
              type="button"
              className="auth__reveal"
              onClick={() => setShowPassword((v) => !v)}
              title={showPassword ? "Скрыть пароль" : "Показать пароль"}
            >
              {showPassword ? "🙈" : "👁️"}
            </button>
          </div>
        </label>

        {isRegister && (
          <label className="auth__field">
            <span className="auth__label">Повторите пароль</span>
            <input
              className="auth__input"
              type={showPassword ? "text" : "password"}
              required
              minLength={6}
              autoComplete="new-password"
              value={passwordConfirm}
              onChange={(e) => setPasswordConfirm(e.target.value)}
              placeholder="Ещё раз тот же пароль"
            />
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
      </form>
    </div>
  );
}