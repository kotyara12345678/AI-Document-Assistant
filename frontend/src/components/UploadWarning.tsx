import { useI18n } from "../i18n";

interface Props {
  onConfirm: () => void;
  onClose: () => void;
}

export default function UploadWarning({ onConfirm, onClose }: Props) {
  const { t } = useI18n();
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal upload-warning"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="upload-warning-title"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="upload-warning__title" id="upload-warning-title">
          {t("warning.title")}
        </div>
        <div className="upload-warning__body">
          <p className="upload-warning__lead">{t("warning.lead")}</p>
          <div className="upload-warning__label">{t("warning.label")}</div>
          <ul className="upload-warning__list">
            <li>{t("warning.passwords")}</li>
            <li>{t("warning.bankData")}</li>
            <li>{t("warning.secrets")}</li>
            <li>{t("warning.sensitive")}</li>
            <li>{t("warning.othersDocs")}</li>
          </ul>
        </div>
        <div className="upload-warning__actions">
          <button type="button" className="btn btn--primary" onClick={onConfirm}>
            {t("warning.ok")}
          </button>
        </div>
      </div>
    </div>
  );
}
