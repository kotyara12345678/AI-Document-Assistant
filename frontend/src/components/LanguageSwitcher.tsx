import { LANGS, useI18n } from "../i18n";

export default function LanguageSwitcher({ className = "" }: { className?: string }) {
  const { lang, setLang, t } = useI18n();
  return (
    <div
      className={`lang-switch${className ? " " + className : ""}`}
      role="group"
      aria-label={t("lang.title")}
      title={t("lang.title")}
    >
      {LANGS.map((l) => (
        <button
          key={l}
          type="button"
          className={l === lang ? "lang-switch__btn lang-switch__btn--active" : "lang-switch__btn"}
          onClick={() => setLang(l)}
          aria-pressed={l === lang}
          title={l === "ru" ? t("lang.ruName") : t("lang.enName")}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </div>
  );
}