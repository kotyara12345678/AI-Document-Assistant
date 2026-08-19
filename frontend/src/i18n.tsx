import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { MESSAGES } from "./i18n/messages";

export type Lang = "ru" | "en";

export const LANGS: Lang[] = ["ru", "en"];
export const LANG_KEY = "ada-lang";

export const PAGE_TITLES: Record<Lang, string> = {
  ru: "ADA — AI Document Assistant",
  en: "ADA — AI Document Assistant",
};

interface TParams {
  [k: string]: string | number;
}

interface LocaleMap {
  ru: string;
  en: string;
}

type PluralForms = Partial<Record<Intl.LDMLPluralRule, LocaleMap>> & {
  other: LocaleMap;
};

type AnyMessage = LocaleMap | PluralForms;

function isLocaleMap(v: AnyMessage): v is LocaleMap {
  return typeof (v as LocaleMap).ru === "string" && typeof (v as LocaleMap).en === "string";
}

function recoverLang(): Lang | null {
  try {
    const saved = localStorage.getItem(LANG_KEY);
    if (saved === "ru" || saved === "en") return saved;
  } catch {
    /* storage unavailable */
  }
  return null;
}

function detectLang(): Lang {
  const saved = recoverLang();
  if (saved) return saved;
  try {
    if ((navigator.language || "").toLowerCase().startsWith("ru")) return "ru";
  } catch {
    /* no navigator (SSR / tests) */
  }
  return "en";
}

/** Module-level current language for non-React code (e.g. api.ts). */
let currentLang: Lang = "en";

export function getCurrentLang(): Lang {
  return currentLang;
}

function applyToDocument(lang: Lang): void {
  try {
    document.documentElement.lang = lang;
  } catch {
    /* not in a DOM */
  }
  try {
    document.title = PAGE_TITLES[lang];
  } catch {
    /* not in a DOM */
  }
}

/** Pre-paint bootstrap: set <html lang> and <title> before the first render. */
export function initDocumentLang(): Lang {
  currentLang = detectLang();
  applyToDocument(currentLang);
  return currentLang;
}

function resolvePath(path: string): AnyMessage | undefined {
  const parts = path.split(".");
  let node: unknown = MESSAGES;
  for (const part of parts) {
    if (node == null || typeof node !== "object") return undefined;
    node = (node as Record<string, unknown>)[part];
  }
  return node as AnyMessage | undefined;
}

function interpolate(template: string, params: TParams): string {
  return template.replace(/\{(\w+)\}/g, (match, key: string) =>
    params[key] !== undefined ? String(params[key]) : match
  );
}

export function translate(path: string, params: TParams = {}, lang?: Lang): string {
  const target = lang ?? currentLang;
  const msg = resolvePath(path);
  if (!msg) return path;
  if (isLocaleMap(msg)) {
    return interpolate(msg[target], params);
  }
  const count = Number(params.count ?? 0);
  let rule: Intl.LDMLPluralRule = "other";
  try {
    rule = new Intl.PluralRules(target).select(count);
  } catch {
    /* keep "other" */
  }
  const forms = msg as PluralForms;
  const form = (forms as Record<string, LocaleMap | undefined>)[rule] ?? forms.other;
  return interpolate(form[target], params);
}

/** Plain-function t() bound to the current language (for non-React code). */
export function t(path: string, params?: TParams): string {
  return translate(path, params);
}

export function formatNumber(n: number, lang: Lang = currentLang): string {
  try {
    return new Intl.NumberFormat(lang).format(n);
  } catch {
    return String(n);
  }
}

export function formatDate(
  value: string | number | Date,
  opts?: Intl.DateTimeFormatOptions,
  lang: Lang = currentLang,
): string {
  try {
    return new Intl.DateTimeFormat(lang, opts).format(new Date(value));
  } catch {
    return String(value);
  }
}

export function formatBytes(bytes: number, _lang: Lang = currentLang): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export function formatChars(n: number, lang: Lang = currentLang): string {
  if (n < 1000) return `${n} ${translate("viewer.charShort", {}, lang)}`;
  return `${(n / 1000).toFixed(1)} ${translate("viewer.charThousand", {}, lang)}`;
}

interface I18nValue {
  lang: Lang;
  setLang: (l: Lang) => void;
  t: (path: string, params?: TParams) => string;
  formatNumber: (n: number) => string;
  formatDate: (value: string | number | Date, opts?: Intl.DateTimeFormatOptions) => string;
  formatBytes: (bytes: number) => string;
  formatChars: (n: number) => string;
}

const I18nContext = createContext<I18nValue>({
  lang: "en",
  setLang: () => undefined,
  t: (p) => translate(p),
  formatNumber: (n) => formatNumber(n),
  formatDate: (d) => formatDate(d),
  formatBytes: (b) => formatBytes(b, "en"),
  formatChars: (n) => formatChars(n, "en"),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => {
    currentLang = detectLang();
    return currentLang;
  });

  useEffect(() => {
    applyToDocument(lang);
    try {
      localStorage.setItem(LANG_KEY, lang);
    } catch {
      /* storage unavailable */
    }
  }, [lang]);

  const value = useMemo<I18nValue>(() => {
    const setLang = (next: Lang) => {
      currentLang = next;
      setLangState(next);
    };
    return {
      lang,
      setLang,
      t: (path, params) => translate(path, params, lang),
      formatNumber: (n) => formatNumber(n, lang),
      formatDate: (d, opts) => formatDate(d, opts, lang),
      formatBytes: (b) => formatBytes(b, lang),
      formatChars: (n) => formatChars(n, lang),
    };
  }, [lang]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  return useContext(I18nContext);
}