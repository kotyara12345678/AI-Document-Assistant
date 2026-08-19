import { useI18n } from "../i18n";

interface Props {
  kind: "privacy" | "cookies";
  onClose: () => void;
}

function PrivacyContent() {
  const { t } = useI18n();
  return (
    <>
      <h3>{t("legal.pr1Title")}</h3>
      <p>{t("legal.pr1p1")}</p>
      <p>{t("legal.pr1p2")}</p>

      <h3>{t("legal.pr2Title")}</h3>
      <p>{t("legal.pr2Lead")}</p>
      <ul>
        <li>{t("legal.pr2li1")}</li>
        <li>{t("legal.pr2li2")}</li>
        <li>{t("legal.pr2li3")}</li>
      </ul>

      <h3>{t("legal.pr3Title")}</h3>
      <p>{t("legal.pr3Lead")}</p>
      <ul>
        <li>{t("legal.pr3li1")}</li>
        <li>{t("legal.pr3li2")}</li>
        <li>{t("legal.pr3li3")}</li>
        <li>{t("legal.pr3li4")}</li>
      </ul>

      <h3>{t("legal.pr4Title")}</h3>
      <p>{t("legal.pr4p1")}</p>

      <h3>{t("legal.pr5Title")}</h3>
      <p>{t("legal.pr5p1")}</p>

      <h3>{t("legal.pr6Title")}</h3>
      <p>{t("legal.pr6Lead")}</p>
      <ul>
        <li>{t("legal.pr6li1")}</li>
        <li>{t("legal.pr6li2")}</li>
        <li>{t("legal.pr6li3")}</li>
        <li>{t("legal.pr6li4")}</li>
      </ul>

      <h3>{t("legal.pr7Title")}</h3>
      <p>{t("legal.pr7p1")}</p>
    </>
  );
}

function CookiesContent() {
  const { t } = useI18n();
  return (
    <>
      <h3>{t("legal.cc1Title")}</h3>
      <p>{t("legal.cc1p1")}</p>

      <h3>{t("legal.cc2Title")}</h3>
      <ul>
        <li>
          <strong>{t("legal.ccNecessaryName")}</strong> — {t("legal.ccNecessaryText")}
        </li>
        <li>
          <strong>{t("legal.ccAnalyticsName")}</strong> — {t("legal.ccAnalyticsText")}
        </li>
        <li>
          <strong>{t("legal.ccPrefsName")}</strong> — {t("legal.ccPrefsText")}
        </li>
      </ul>

      <h3>{t("legal.cc3Title")}</h3>
      <p>{t("legal.cc3p1")}</p>
    </>
  );
}

export default function LegalPage({ kind, onClose }: Props) {
  const { t } = useI18n();
  const title = kind === "privacy" ? t("legal.titlePrivacy") : t("legal.titleCookies");
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal legal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="legal__head">
          <div className="legal__brand">ADA</div>
          <button type="button" className="legal__close" onClick={onClose} aria-label={t("legal.closeAria")}>
            ✕
          </button>
        </div>
        <div className="legal__title">{title}</div>
        <div className="legal__body">
          <p className="legal__updated">{t("legal.updated")}</p>
          {kind === "privacy" ? <PrivacyContent /> : <CookiesContent />}
        </div>
        <div className="legal__actions">
          <button type="button" className="btn btn--primary" onClick={onClose}>
            {t("legal.close")}
          </button>
        </div>
      </div>
    </div>
  );
}
