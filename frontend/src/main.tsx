import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// Apply the persisted theme before first paint to avoid a flash.
// Default (first visit) is light.
const THEME_KEY = "docsearch-theme";
try {
  const saved = localStorage.getItem(THEME_KEY);
  document.documentElement.dataset.theme = saved === "dark" ? "dark" : "light";
} catch {
  document.documentElement.dataset.theme = "light";
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
