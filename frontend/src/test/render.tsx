import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";
import { I18nProvider, LANG_KEY, type Lang } from "../i18n";

export function renderWithI18n(
  ui: ReactElement,
  lang: Lang = "ru",
  options?: Omit<RenderOptions, "wrapper">,
) {
  try {
    localStorage.setItem(LANG_KEY, lang);
  } catch {
    /* storage unavailable */
  }
  return render(ui, {
    wrapper: ({ children }) => <I18nProvider>{children}</I18nProvider>,
    ...options,
  });
}
