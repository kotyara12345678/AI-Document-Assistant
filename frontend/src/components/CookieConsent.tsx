import { useState } from "react";
import {
  DEFAULT_COOKIE_SETTINGS,
  FULL_COOKIE_SETTINGS,
  hasCookieChoice,
  saveCookieSettings,
  type CookieSettings,
} from "../consent";
import { useI18n } from "../i18n";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export default function CookieConsent({ open, onOpenChange }: Props) {
  const { t } = useI18n();
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
        <div className="cookie-banner" role="dialog" aria-live="polite" aria-label={t("cookieConsent.bannerAria")}>
          <div className="cookie-banner__body">
            <p className="cookie-banner__text">{t("cookieConsent.bannerText")}</p>
            <div className="cookie-banner__actions">
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => apply(FULL_COOKIE_SETTINGS)}
              >
                {t("cookieConsent.accept")}
              </button>
              <button type="button" className="btn" onClick={openSettings}>
                {t("cookieConsent.settings")}
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
            aria-label={t("cookieConsent.modalAria")}
            onClick={(e) => e.stopPropagation()}
          >
            <div className="cookie-settings__title">{t("cookieConsent.modalTitle")}</div>
            <p className="cookie-settings__intro">{t("cookieConsent.intro")}</p>

            <label className="cookie-option">
              <input type="checkbox" checked={true} disabled />
              <span className="cookie-option__body">
                <span className="cookie-option__name">{t("cookieConsent.necessaryName")}</span>
                <span className="cookie-option__desc">{t("cookieConsent.necessaryDesc")}</span>
              </span>
            </label>

            <label className="cookie-option">
              <input
                type="checkbox"
                checked={draft.analytics}
                onChange={(e) => setDraft({ ...draft, analytics: e.target.checked })}
              />
              <span className="cookie-option__body">
                <span className="cookie-option__name">{t("cookieConsent.analyticsName")}</span>
                <span className="cookie-option__desc">{t("cookieConsent.analyticsDesc")}</span>
              </span>
            </label>

            <label className="cookie-option">
              <input
                type="checkbox"
                checked={draft.preferences}
                onChange={(e) => setDraft({ ...draft, preferences: e.target.checked })}
              />
              <span className="cookie-option__body">
                <span className="cookie-option__name">{t("cookieConsent.preferencesName")}</span>
                <span className="cookie-option__desc">{t("cookieConsent.preferencesDesc")}</span>
              </span>
            </label>

            <div className="cookie-settings__actions">
              <button type="button" className="btn btn--primary" onClick={() => apply(draft)}>
                {t("cookieConsent.save")}
              </button>
              <button type="button" className="btn" onClick={() => onOpenChange(false)}>
                {t("cookieConsent.cancel")}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
