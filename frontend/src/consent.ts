export interface CookieSettings {
  necessary: boolean;
  analytics: boolean;
  preferences: boolean;
}

export const COOKIE_KEY = "ada-cookie-consent";
export const UPLOAD_WARNING_KEY = "ada-upload-warning-seen";

export const DEFAULT_COOKIE_SETTINGS: CookieSettings = {
  necessary: true,
  analytics: false,
  preferences: false,
};

export const FULL_COOKIE_SETTINGS: CookieSettings = {
  necessary: true,
  analytics: true,
  preferences: true,
};

function parseSettings(raw: string): CookieSettings | null {
  try {
    const parsed = JSON.parse(raw) as Partial<CookieSettings>;
    return {
      necessary: parsed.necessary !== false,
      analytics: parsed.analytics === true,
      preferences: parsed.preferences === true,
    };
  } catch {
    return null;
  }
}

export function getCookieSettings(): CookieSettings | null {
  try {
    const raw = localStorage.getItem(COOKIE_KEY);
    if (!raw) return null;
    return parseSettings(raw);
  } catch {
    return null;
  }
}

export function hasCookieChoice(): boolean {
  return getCookieSettings() !== null;
}

export function saveCookieSettings(settings: CookieSettings): void {
  try {
    localStorage.setItem(COOKIE_KEY, JSON.stringify(settings));
  } catch {
    /* storage unavailable */
  }
}

export function hasSeenUploadWarning(): boolean {
  try {
    return localStorage.getItem(UPLOAD_WARNING_KEY) === "1";
  } catch {
    return true;
  }
}

export function markUploadWarningSeen(): void {
  try {
    localStorage.setItem(UPLOAD_WARNING_KEY, "1");
  } catch {
    /* storage unavailable */
  }
}
