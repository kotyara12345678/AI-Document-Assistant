import { useState } from "react";
import {
  DEFAULT_COOKIE_SETTINGS,
  FULL_COOKIE_SETTINGS,
  hasCookieChoice,
  saveCookieSettings,
  type CookieSettings,
} from "../consent";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function CookieConsent({ open, onOpenChange }: Props) {
  const [chosen, setChosen] = useState<boolean>(() => hasCookieChoice());
  const [draft, setDraft] = useState<CookieSettings>(DEFAULT_COOKIE_SETTINGS);

  const apply = (settings: CookieSettings) => {
    saveCookieSettings(settings);
    setChosen(true);
    onOpenChange(false);
  };

  const openSettings = () => {
    setDraft({ ...DEFAULT_COOKIE_SETTINGS });
    onOpenChange(true);
  };

  return (
    <>
      {!chosen && !open && (
        <div className="cookie-banner" role="dialog" aria-live="polite" aria-label="Использование файлов cookie">
          <div className="cookie-banner__body">
            <p className="cookie-banner__text">
              Мы используем файлы cookie для обеспечения работы сайта и улучшения сервиса.
            </p>
            <div className="cookie-banner__actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => apply(FULL_COOKIE_SETTINGS)}
              >
                Принять
              </button>
              <button type="button" className="btn" onClick={openSettings}>
                Настроить
              </button>
            </div>
          </div>
        </div>
      )}

      {open && (
        <div className="modal-backdrop" onClick={() => onOpenChange(false)}>
          <div
            className="modal cookie-settings"
            role="dialog"
            aria-modal="true"
            aria-label="Настройки файлов cookie"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="cookie-settings__title">Настройки файлов cookie</div>
            <p className="cookie-settings__intro">
              Мы используем файлы cookie для работы и улучшения сервиса. Вы можете выбрать,
              какие категории разрешить.
            </p>

            <label className="cookie-option">
              <input type="checkbox" checked={true} disabled />
              <span className="cookie-option__body">
                <span className="cookie-option__name">Необходимые</span>
                <span className="cookie-option__desc">
                  Обеспечивают работу сервиса: вход в аккаунт, безопасность и сохранение сессии.
                  Их нельзя отключить.
                </span>
              </span>
            </label>

            <label className="cookie-option">
              <input
                type="checkbox"
                checked={draft.analytics}
                onChange={(e) => setDraft({ ...draft, analytics: e.target.checked })}
              />
              <span className="cookie-option__body">
                <span className="cookie-option__name">Аналитика</span>
                <span className="cookie-option__desc">
                  Помогают понимать, как используется сервис, чтобы улучшать его.
                </span>
              </span>
            </label>

            <label className="cookie-option">
              <input
                type="checkbox"
                checked={draft.preferences}
                onChange={(e) => setDraft({ ...draft, preferences: e.target.checked })}
              />
              <span className="cookie-option__body">
                <span className="cookie-option__name">Персональные настройки</span>
                <span className="cookie-option__desc">
                  Запоминают ваши предпочтения, например тему оформления.
                </span>
              </span>
            </label>

            <div className="cookie-settings__actions">
              <button type="button" className="btn btn--primary" onClick={() => apply(draft)}>
                Сохранить настройки
              </button>
              <button type="button" className="btn" onClick={() => onOpenChange(false)}>
                Отмена
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
