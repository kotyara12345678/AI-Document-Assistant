import { afterEach, describe, expect, it } from "vitest";
import {
  COOKIE_KEY,
  DEFAULT_COOKIE_SETTINGS,
  FULL_COOKIE_SETTINGS,
  UPLOAD_WARNING_KEY,
  getCookieSettings,
  hasCookieChoice,
  hasSeenUploadWarning,
  markUploadWarningSeen,
  saveCookieSettings,
} from "../consent";

afterEach(() => {
  localStorage.clear();
});

describe("cookie consent storage", () => {
  it("no stored choice until saved", () => {
    expect(hasCookieChoice()).toBe(false);
    expect(getCookieSettings()).toBeNull();
  });

  it("round-trips saved settings", () => {
    saveCookieSettings({ ...DEFAULT_COOKIE_SETTINGS, analytics: true });
    expect(hasCookieChoice()).toBe(true);
    expect(getCookieSettings()).toEqual({ necessary: true, analytics: true, preferences: false });
  });

  it("accept all stores full settings", () => {
    saveCookieSettings(FULL_COOKIE_SETTINGS);
    expect(getCookieSettings()).toEqual(FULL_COOKIE_SETTINGS);
  });

  it("malformed stored JSON is treated as no choice", () => {
    localStorage.setItem(COOKIE_KEY, "{not-json");
    expect(getCookieSettings()).toBeNull();
    expect(hasCookieChoice()).toBe(false);
  });
});

describe("first-upload warning storage", () => {
  it("not seen by default", () => {
    expect(hasSeenUploadWarning()).toBe(false);
  });

  it("seen after marking", () => {
    markUploadWarningSeen();
    expect(hasSeenUploadWarning()).toBe(true);
    expect(localStorage.getItem(UPLOAD_WARNING_KEY)).toBe("1");
  });

  it("any stored value other than '1' means not seen", () => {
    localStorage.setItem(UPLOAD_WARNING_KEY, "yes");
    expect(hasSeenUploadWarning()).toBe(false);
  });
});