import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useI18n } from "../i18n";
import Reveal from "./Reveal";
import LanguageSwitcher from "./LanguageSwitcher";

const BRAND = "ADA — AI Document Assistant";

interface NavItem {
  id: string;
  labelKey: string;
}

const NAV_IDS: NavItem[] = [
  { id: "overview", labelKey: "docs.navOverview" },
  { id: "getting-started", labelKey: "docs.navGettingStarted" },
  { id: "features", labelKey: "docs.navFeatures" },
  { id: "benefits", labelKey: "docs.navBenefits" },
  { id: "privacy", labelKey: "docs.navPrivacy" },
  { id: "roadmap", labelKey: "docs.navRoadmap" },
];

function CheckItem({ children }: { children: ReactNode }) {
  return (
    <li className="docs-check">
      <span className="docs-check__mark">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="m4 12.5 5 5L20 6.5" />
        </svg>
      </span>
      <span className="docs-check__text">{children}</span>
    </li>
  );
}

function Section({
  id,
  kicker,
  title,
  sub,
  children,
  wide = false,
}: {
  id: string;
  kicker: string;
  title: string;
  sub?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <section className="docs__section" id={id}>
      <div className="docs__wrap">
        <Reveal>
          <div className="docs__section-head">
            <span className="docs__section-kicker">{kicker}</span>
            <h2 className="docs__section-title">{title}</h2>
            {sub && <p className="docs__section-sub">{sub}</p>}
          </div>
        </Reveal>
        <Reveal delay={80} className={wide ? "docs__section-body docs__section-body--wide" : "docs__section-body"}>
          {children}
        </Reveal>
      </div>
    </section>
  );
}

function RoadmapBlock() {
  const { t } = useI18n();
  const items = useMemo(
    () => [
      { status: t("docs.r1Status"), tone: "now" as const, title: t("docs.r1Title"), text: t("docs.r1Text") },
      { status: t("docs.r2Status"), tone: "soon" as const, title: t("docs.r2Title"), text: t("docs.r2Text") },
      { status: t("docs.r3Status"), tone: "next" as const, title: t("docs.r3Title"), text: t("docs.r3Text") },
      { status: t("docs.r4Status"), tone: "later" as const, title: t("docs.r4Title"), text: t("docs.r4Text") },
    ],
    [t],
  );

  return (
    <div className="docs-roadmap">
      {items.map((item, i) => (
        <div className={`docs-roadmap__item docs-roadmap__item--${item.tone}`} key={item.status}>
          <span className="docs-roadmap__node" />
          <div className="docs-roadmap__body">
            <span className="docs-roadmap__status">{item.status}</span>
            <span className="docs-roadmap__title">{item.title}</span>
            <span className="docs-roadmap__text">{item.text}</span>
          </div>
          <span className="docs-roadmap__index">{String(i + 1).padStart(2, "0")}</span>
        </div>
      ))}
    </div>
  );
}

function useScrollspy(ids: string[]) {
  const [active, setActive] = useState(ids[0] ?? "");
  const idList = useMemo(() => ids.join(","), [ids]);

  useEffect(() => {
    const list = idList.split(",");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) setActive(entry.target.id);
        });
      },
      { rootMargin: "-25% 0px -65% 0px", threshold: 0 }
    );
    list.forEach((id) => {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    });
    return () => observer.disconnect();
  }, [idList]);

  return active;
}

export default function DocsPage({ onHome }: { onHome: () => void }) {
  const { t } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);

  const nav = useMemo(
    () => NAV_IDS.map((item) => ({ id: item.id, label: t(item.labelKey) })),
    [t],
  );

  const sectionIds = useMemo(() => nav.map((item) => item.id), [nav]);
  const active = useScrollspy(sectionIds);

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="docs">
      <header className="docs__nav">
        <div className="docs__nav-inner">
          <button type="button" className="docs__brand" onClick={onHome} title={t("docs.homeTitle")}>
            ADA
            <span className="docs__brand-sub">{t("docs.brandSub")}</span>
          </button>

          <nav className={`docs__links ${menuOpen ? "docs__links--open" : ""}`}>
            {nav.map((item) => (
              <button
                type="button"
                key={item.id}
                className={active === item.id ? "docs__nav-link docs__nav-link--active" : "docs__nav-link"}
                onClick={() => scrollTo(item.id)}
              >
                {item.label}
              </button>
            ))}
          </nav>

          <LanguageSwitcher />

          <button
            type="button"
            className={`docs__burger ${menuOpen ? "docs__burger--open" : ""}`}
            onClick={() => setMenuOpen((v) => !v)}
            aria-label={t("landing.menu")}
            aria-expanded={menuOpen}
          >
            <i />
            <i />
          </button>
        </div>
      </header>

      <div className="docs__layout">
        <aside className={`docs__toc ${menuOpen ? "docs__toc--open" : ""}`}>
          <span className="docs__toc-title">{t("docs.tocTitle")}</span>
          {nav.map((item) => (
            <button
              type="button"
              key={item.id}
              className={`docs__toc-link ${active === item.id ? "docs__toc-link--active" : ""}`}
              onClick={() => scrollTo(item.id)}
            >
              <span className="docs__toc-dot" />
              {item.label}
            </button>
          ))}
          <div className="docs__toc-back">
            <button type="button" className="btn btn--ghost" onClick={onHome}>
              {t("docs.backHome")}
            </button>
          </div>
        </aside>

        <main className="docs__content">
          <Section id="overview" kicker={t("docs.overviewKicker")} title={t("docs.overviewTitle")}>
            <p className="docs__lead">{t("docs.overviewLead1", { brand: BRAND })}</p>
            <p>{t("docs.overviewLead2")}</p>
            <ul className="docs__list docs__list--grid">
              <CheckItem>{t("docs.checkLoad")}</CheckItem>
              <CheckItem>{t("docs.checkAsk")}</CheckItem>
              <CheckItem>{t("docs.checkSources")}</CheckItem>
              <CheckItem>{t("docs.checkSemantic")}</CheckItem>
              <CheckItem>{t("docs.checkCreate")}</CheckItem>
              <CheckItem>{t("docs.checkEdit")}</CheckItem>
              <CheckItem>{t("docs.checkMulti")}</CheckItem>
              <CheckItem>{t("docs.checkHistory")}</CheckItem>
            </ul>
            <div className="docs__formats" style={{ marginTop: 28 }}>
              <span className="docs__format">PDF</span>
              <span className="docs__format">DOCX</span>
              <span className="docs__format">ODT</span>
              <span className="docs__format">TXT</span>
              <span className="docs__format docs__format--muted">Markdown</span>
            </div>
          </Section>

          <Section
            id="getting-started"
            kicker={t("docs.gsKicker")}
            title={t("docs.gsTitle")}
            sub={t("docs.gsSub")}
          >
            <ul className="docs__list">
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>{t("docs.gsStep1Strong")}</strong>
                  {t("docs.gsStep1")}
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>{t("docs.gsStep2Strong")}</strong>
                  {t("docs.gsStep2")}
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>{t("docs.gsStep3Strong")}</strong>
                  {t("docs.gsStep3")}
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>{t("docs.gsStep4Strong")}</strong>
                  {t("docs.gsStep4")}
                </span>
              </li>
            </ul>
            <div className="docs__cols">
              <div className="docs__card">
                <h3>{t("docs.gsC1Title")}</h3>
                <p>{t("docs.gsC1Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.gsC2Title")}</h3>
                <p>{t("docs.gsC2Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.gsC3Title")}</h3>
                <p>{t("docs.gsC3Text")}</p>
              </div>
            </div>
          </Section>

          <Section id="features" kicker={t("docs.featuresKicker")} title={t("docs.featuresTitle")}>
            <div className="docs__cols">
              <div className="docs__card">
                <h3>{t("docs.f1Title")}</h3>
                <p>{t("docs.f1Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.f2Title")}</h3>
                <p>{t("docs.f2Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.f3Title")}</h3>
                <p>{t("docs.f3Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.f4Title")}</h3>
                <p>{t("docs.f4Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.f5Title")}</h3>
                <p>{t("docs.f5Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.f6Title")}</h3>
                <p>{t("docs.f6Text")}</p>
              </div>
            </div>
          </Section>

          <Section id="benefits" kicker={t("docs.benefitsKicker")} title={t("docs.benefitsTitle")}>
            <div className="docs__cols">
              <div className="docs__card">
                <h3>{t("docs.b1Title")}</h3>
                <p>{t("docs.b1Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.b2Title")}</h3>
                <p>{t("docs.b2Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.b3Title")}</h3>
                <p>{t("docs.b3Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.b4Title")}</h3>
                <p>{t("docs.b4Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.b5Title")}</h3>
                <p>{t("docs.b5Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.b6Title")}</h3>
                <p>{t("docs.b6Text")}</p>
              </div>
            </div>
          </Section>

          <Section id="privacy" kicker={t("docs.privacyKicker")} title={t("docs.privacyTitle")}>
            <div className="docs__cols">
              <div className="docs__card">
                <h3>{t("docs.p1Title")}</h3>
                <p>{t("docs.p1Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.p2Title")}</h3>
                <p>{t("docs.p2Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.p3Title")}</h3>
                <p>{t("docs.p3Text")}</p>
              </div>
              <div className="docs__card">
                <h3>{t("docs.p4Title")}</h3>
                <p>{t("docs.p4Text")}</p>
              </div>
            </div>
          </Section>

          <Section id="roadmap" kicker={t("docs.roadmapKicker")} title={t("docs.roadmapTitle")}>
            <p>{t("docs.roadmapSub")}</p>
            <RoadmapBlock />
          </Section>

          <footer className="docs__footer">
            <div className="docs__footer-brand">
              <span className="docs__footer-logo">ADA</span>
              <p className="docs__footer-tagline">{t("docs.footerTagline")}</p>
            </div>
            <nav className="docs__footer-col">
              <span className="docs__footer-heading">{t("docs.footerNavHeading")}</span>
              <button type="button" onClick={() => scrollTo("overview")}>{t("docs.navOverview")}</button>
              <button type="button" onClick={() => scrollTo("getting-started")}>{t("docs.navGettingStarted")}</button>
              <button type="button" onClick={() => scrollTo("privacy")}>{t("docs.navPrivacy")}</button>
            </nav>
            <div className="docs__footer-col">
              <span className="docs__footer-heading">{t("docs.footerCodeHeading")}</span>
              <span className="docs__footer-note">{t("docs.footerCodeNote")}</span>
            </div>
            <div className="docs__footer-col">
              <span className="docs__footer-heading">{t("docs.footerContactsHeading")}</span>
              <button type="button" onClick={onHome}>{t("docs.footerContact")}</button>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}
