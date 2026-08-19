import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { I18nProvider, initDocumentLang } from "./i18n";
import "./styles.css";

// Apply the persisted theme and language before first paint to avoid a flash.
// Theme default (first visit) is light; language follows the browser unless
// the user chose one explicitly.
const THEME_KEY = "docsearch-theme";
try {
  const saved = localStorage.getItem(THEME_KEY);
  document.documentElement.dataset.theme = saved === "dark" ? "dark" : "light";
} catch {
  document.documentElement.dataset.theme = "light";
}
try {
  initDocumentLang();
} catch {
  /* keep the static <html lang> fallback */
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>
);