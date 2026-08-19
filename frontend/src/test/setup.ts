import { beforeEach } from "vitest";
import { LANG_KEY, initDocumentLang } from "../i18n";

beforeEach(() => {
  try {
    localStorage.setItem(LANG_KEY, "ru");
  } catch {
    /* jsdom may restrict storage in some environments */
  }
  initDocumentLang();
});
