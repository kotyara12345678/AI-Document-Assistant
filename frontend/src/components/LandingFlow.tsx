import { useState } from "react";
import type { UserOut } from "../types";
import AuthScreen from "./AuthScreen";
import CookieConsent from "./CookieConsent";
import DocsPage from "./DocsPage";
import LandingPage from "./LandingPage";
import LegalPage from "./LegalPage";

interface Props {
  onAuthed: (user: UserOut) => void;
}

type View = "landing" | "auth" | "docs";
type Mode = "login" | "register";
type LegalKind = "privacy" | "cookies";

export default function LandingFlow({ onAuthed }: Props) {
  const [view, setView] = useState<View>("landing");
  const [authMode, setAuthMode] = useState<Mode>("register");
  const [legal, setLegal] = useState<LegalKind | null>(null);
  const [cookieSettingsOpen, setCookieSettingsOpen] = useState(false);

  const goAuth = (mode: Mode) => {
    setAuthMode(mode);
    setView("auth");
  };

  return (
    <div className="landing">
      {view === "landing" ? (
        <LandingPage
          onLogin={() => goAuth("login")}
          onRegister={() => goAuth("register")}
          onOpenPrivacy={() => setLegal("privacy")}
          onOpenCookies={() => setLegal("cookies")}
          onOpenCookieSettings={() => setCookieSettingsOpen(true)}
          onOpenDocs={() => setView("docs")}
        />
      ) : view === "docs" ? (
        <DocsPage onHome={() => setView("landing")} />
      ) : (
        <AuthScreen
          initialMode={authMode}
          onAuthed={onAuthed}
          onBack={() => setView("landing")}
          onOpenPrivacy={() => setLegal("privacy")}
          onOpenCookies={() => setLegal("cookies")}
        />
      )}

      <CookieConsent open={cookieSettingsOpen} onOpenChange={setCookieSettingsOpen} />

      {legal && <LegalPage kind={legal} onClose={() => setLegal(null)} />}
    </div>
  );
}