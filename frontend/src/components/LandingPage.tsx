import { useState } from "react";
import type { CSSProperties } from "react";
import Reveal, { useReveal } from "./Reveal";

interface Props {
  onLogin: () => void;
  onRegister: () => void;
  onOpenPrivacy: () => void;
  onOpenCookies: () => void;
  onOpenCookieSettings: () => void;
  onOpenDocs: () => void;
}

interface IconProps {
  size?: number;
}

function IconDocs({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 3h7l4 4v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v4h4" />
      <path d="M9.5 12h5M9.5 15.5h5" />
    </svg>
  );
}

function IconSearch({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-3.6-3.6" />
      <path d="M8.5 11h5M11 8.5v5" />
    </svg>
  );
}

function IconAnalyze({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 4h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z" />
      <path d="M8 9h8M8 12.5h5" />
      <path d="M17.5 16.5l1 1M18.5 15.2c.7.7.7 1.8 0 2.5" />
    </svg>
  );
}

function IconEdit({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 3h12a1 1 0 0 1 1 1v13l-4 4H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M18 17h-4v4" />
      <path d="M8 9h6M8 12.5h4" />
    </svg>
  );
}

function IconGenerate({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v6M9 6h6" />
      <circle cx="12" cy="16" r="4.5" />
      <path d="M12 13.5v5M9.5 16h5" />
    </svg>
  );
}

function IconShield({ size = 22 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3l7 2.8v5.4c0 4.3-2.8 7.6-7 9.8-4.2-2.2-7-5.5-7-9.8V5.8L12 3Z" />
      <path d="m9 12 2 2 4-4.5" />
    </svg>
  );
}

function IconMagnifier({ size = 14 }: IconProps) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.2-4.2" />
    </svg>
  );
}

function IconDoc({ size = 14, kind = "pdf" }: IconProps & { kind?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M7 3h7l4 4v12a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
      <path d="M14 3v4h4" />
      {kind === "pdf" && <path d="M9.5 12h.01M12 12h.01M14.5 12h.01" />}
    </svg>
  );
}

interface Feature {
  icon: (p: IconProps) => JSX.Element;
  title: string;
  text: string;
}

const FEATURES: Feature[] = [
  { icon: IconDocs, title: "Работа с документами", text: "PDF, DOCX, ODT, TXT и другие поддерживаемые форматы." },
  { icon: IconSearch, title: "AI-поиск", text: "Поиск информации внутри пользовательских документов." },
  { icon: IconAnalyze, title: "Анализ документов", text: "Ответы на вопросы по содержимому загруженных файлов." },
  { icon: IconEdit, title: "Редактирование", text: "Изменение текста и структуры документов с сохранением исходного файла." },
  { icon: IconGenerate, title: "Генерация документов", text: "Создание новых документов на основе предоставленной информации." },
  { icon: IconShield, title: "Мгновенный ответ", text: "Один вопрос — и нужные фрагменты из ваших файлов уже перед глазами." },
];

interface Step {
  icon: (p: IconProps) => JSX.Element;
  title: string;
  text: string;
}

const STEPS: Step[] = [
  { icon: IconDocs, title: "Документ", text: "Загрузите файлы в личное пространство" },
  { icon: IconAnalyze, title: "Обработка", text: "Содержимое извлекается и разбивается на фрагменты" },
  { icon: IconSearch, title: "Поиск", text: "Векторный индекс находит релевантные места" },
  { icon: IconGenerate, title: "AI", text: "Модель собирает структурированный ответ" },
  { icon: IconShield, title: "Результат", text: "Ответ, источники и выделенные фрагменты" },
];

function HeroVisual() {
  return (
    <div className="ada-visual" aria-hidden="true">
      <svg className="ada-visual__mesh" viewBox="0 0 520 440" fill="none">
        <g stroke="var(--lnd-line)" strokeWidth="1">
          <line x1="120" y1="70" x2="250" y2="160" />
          <line x1="250" y1="160" x2="150" y2="300" />
          <line x1="150" y1="300" x2="330" y2="380" />
          <line x1="120" y1="70" x2="60" y2="230" />
          <line x1="60" y1="230" x2="150" y2="300" />
          <line x1="250" y1="160" x2="430" y2="120" />
          <line x1="430" y1="120" x2="330" y2="380" />
          <line x1="330" y1="380" x2="470" y2="300" />
          <line x1="470" y1="300" x2="430" y2="120" />
        </g>
        <g fill="var(--lnd-line-strong)">
          <circle cx="120" cy="70" r="3" />
          <circle cx="250" cy="160" r="3" />
          <circle cx="60" cy="230" r="3" />
          <circle cx="150" cy="300" r="3" />
          <circle cx="430" cy="120" r="3" />
          <circle cx="470" cy="300" r="3" />
          <circle cx="330" cy="380" r="3" />
        </g>
        <circle className="ada-visual__node" cx="110" cy="44" r="3.5" fill="var(--lnd-accent)" />
        <circle className="ada-visual__node ada-visual__node--pulse" cx="306" cy="282" r="3.5" fill="var(--lnd-accent)" />
      </svg>

      <div className="ada-visual__panel">
        <div className="ada-visual__head">
          <span className="ada-visual__dots">
            <i />
            <i />
            <i />
          </span>
          <span className="ada-visual__head-title">ADA · Поиск по документам</span>
        </div>

        <div className="ada-visual__search">
          <IconMagnifier />
          <span className="ada-visual__search-text">конфиденциальность в догово…</span>
          <span className="ada-visual__search-caret" />
        </div>

        <div className="ada-visual__docs">
          <div className="ada-visual__doc">
            <span className="ada-visual__doc-icon ada-visual__doc-icon--pdf"><IconDoc kind="pdf" /></span>
            <span className="ada-visual__doc-name">договор-аренды.pdf</span>
            <span className="ada-visual__doc-score">74%</span>
          </div>
          <div className="ada-visual__doc ada-visual__doc--active">
            <span className="ada-visual__doc-icon ada-visual__doc-icon--docx"><IconDoc kind="docx" /></span>
            <span className="ada-visual__doc-name">регламент.docx</span>
            <span className="ada-visual__doc-score">96%</span>
          </div>
          <div className="ada-visual__doc">
            <span className="ada-visual__doc-icon ada-visual__doc-icon--txt"><IconDoc kind="txt" /></span>
            <span className="ada-visual__doc-name">примечания.txt</span>
            <span className="ada-visual__doc-score">41%</span>
          </div>
        </div>

        <div className="ada-visual__fragment">
          <div className="ada-visual__fragment-label">
            <span className="ada-visual__pulse" />
            Фрагмент · вектор №41
          </div>
          <p className="ada-visual__fragment-text">
            «…не передавать <mark className="ada-visual__mark">конфиденциальные данные</mark> третьим лицам без
            письменного согласия…» <span className="ada-visual__fragment-src">регламент.docx</span>
          </p>
        </div>

        <div className="ada-visual__result">
          <span className="ada-visual__result-dot" />
          <span className="ada-visual__result-text">Ответ построен из 3 источников</span>
          <span className="ada-visual__result-time">0.8 сек</span>
        </div>
      </div>
    </div>
  );
}

export default function LandingPage({ onLogin, onRegister, onOpenPrivacy, onOpenCookies, onOpenCookieSettings, onOpenDocs }: Props) {
  const [menuOpen, setMenuOpen] = useState(false);
  const heroRef = useReveal<HTMLDivElement>();

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="landing">
      <SCGradient />

      <header className="landing__nav">
        <div className="landing__nav-inner">
          <button type="button" className="landing__brand" onClick={() => scrollTo("top")}>
            ADA
          </button>

          <nav className={`landing__links ${menuOpen ? "landing__links--open" : ""}`}>
            <button type="button" onClick={() => scrollTo("features")}>Возможности</button>
            <button type="button" onClick={() => scrollTo("how")}>Как работает</button>
            <button type="button" onClick={onOpenDocs}>Документация</button>
          </nav>

          <div className="landing__auth">
            <button type="button" className="btn btn--ghost" onClick={onLogin}>Войти</button>
            <button type="button" className="btn btn--primary" onClick={onRegister}>Регистрация</button>
          </div>

          <button
            type="button"
            className={`landing__burger ${menuOpen ? "landing__burger--open" : ""}`}
            onClick={() => setMenuOpen((v) => !v)}
            aria-label="Меню"
            aria-expanded={menuOpen}
          >
            <i />
            <i />
          </button>
        </div>
      </header>

      <main>
        <section className="landing__hero" id="top">
          <div className="landing__hero-grid" />
          <div className="landing__wrap landing__hero-inner">
            <div className="landing__hero-copy">
              <div ref={heroRef.ref} className={`landing__hero-kicker ${heroRef.visible ? "reveal--visible" : ""}`}>
                <span className="landing__hero-kicker-line" />
                Интеллектуальная работа с документами
              </div>
              <h1 className={`landing__hero-title ${heroRef.visible ? "reveal--visible" : ""}`} style={{ transitionDelay: "80ms" }}>
                ADA
              </h1>
              <p className={`landing__hero-sub ${heroRef.visible ? "reveal--visible" : ""}`} style={{ transitionDelay: "160ms" }}>
                AI Document Assistant
              </p>
              <p className={`landing__hero-text ${heroRef.visible ? "reveal--visible" : ""}`} style={{ transitionDelay: "240ms" }}>
                Загружайте документы. Ищите информацию. Анализируйте содержимое. Редактируйте документы с помощью AI.
              </p>
              <div className={`landing__hero-actions ${heroRef.visible ? "reveal--visible" : ""}`} style={{ transitionDelay: "320ms" }}>
                <button type="button" className="btn btn--primary btn--lg" onClick={onRegister}>
                  Начать работу
                </button>
                <button type="button" className="btn btn--ghost btn--lg" onClick={() => scrollTo("features")}>
                  Возможности
                </button>
              </div>
            </div>

            <div className={`landing__hero-visual ${heroRef.visible ? "reveal--visible" : ""}`} style={{ transitionDelay: "200ms" }}>
              <HeroVisual />
            </div>
          </div>
        </section>

        <section className="landing__section" id="features">
          <div className="landing__wrap">
            <Reveal>
              <div className="landing__section-head">
                <span className="landing__section-kicker">Возможности</span>
                <h2 className="landing__section-title">Всё, что нужно для работы с документами</h2>
                <p className="landing__section-sub">Один рабочий интерфейс для хранения, поиска, анализа и редактирования документов.</p>
              </div>
            </Reveal>
            <div className="landing__features">
              {FEATURES.map((f, i) => {
                const Icon = f.icon;
                return (
                  <Reveal key={f.title} delay={(i % 3) * 90}>
                    <div className="feature-card">
                      <span className="feature-card__icon"><Icon /></span>
                      <h3 className="feature-card__title">{f.title}</h3>
                      <p className="feature-card__text">{f.text}</p>
                    </div>
                  </Reveal>
                );
              })}
            </div>
          </div>
        </section>

        <section className="landing__section landing__section--soft" id="how">
          <div className="landing__wrap">
            <Reveal>
              <div className="landing__section-head">
                <span className="landing__section-kicker">Как это работает</span>
                <h2 className="landing__section-title">От файла — к результату</h2>
              </div>
            </Reveal>
            <div className="landing__pipeline">
              <span className="pipeline-comet" aria-hidden="true" />
              {STEPS.map((s, i) => {
                const Icon = s.icon;
                return (
                  <Reveal key={s.title} delay={i * 110}>
                    <div className="pipeline-step" style={{ "--i": i } as CSSProperties}>
                      <span className="pipeline-step__index">{String(i + 1).padStart(2, "0")}</span>
                      <span className="pipeline-step__icon"><Icon /></span>
                      <span className="pipeline-step__title">{s.title}</span>
                      <span className="pipeline-step__text">{s.text}</span>
                    </div>
                  </Reveal>
                );
              })}
            </div>
          </div>
        </section>

        <section className="landing__cta">
          <div className="landing__cta-grid" />
          <div className="landing__wrap landing__cta-inner">
            <Reveal>
              <h2 className="landing__cta-title">Умный поиск в ваших документах за секунды.</h2>
              <p className="landing__cta-sub">Загрузите первый файл — остальное сделает ADA.</p>
              <div className="landing__cta-actions">
                <button type="button" className="btn btn--primary btn--lg" onClick={onRegister}>
                  Начать работу
                </button>
              </div>
            </Reveal>
          </div>
        </section>
      </main>

      <footer className="landing__footer">
        <div className="landing__wrap landing__footer-inner">
          <div className="landing__footer-brand">
            <span className="landing__footer-logo">ADA</span>
            <p className="landing__footer-tagline">AI Document Assistant — хранение, поиск и анализ документов.</p>
          </div>
          <nav className="landing__footer-col">
            <span className="landing__footer-heading">Сервис</span>
            <button type="button" onClick={() => scrollTo("features")}>Возможности</button>
            <button type="button" onClick={() => scrollTo("how")}>Как работает</button>
            <button type="button" onClick={onOpenDocs}>Документация</button>
          </nav>
          <nav className="landing__footer-col">
            <span className="landing__footer-heading">Документы</span>
            <button type="button" onClick={onOpenPrivacy}>Политика обработки персональных данных</button>
            <button type="button" onClick={onOpenCookies}>Cookie Policy</button>
            <button type="button" onClick={onOpenCookieSettings}>Настроить cookie</button>
          </nav>
        </div>
        <div className="landing__wrap landing__footer-bottom">
          <span>© 2026 ADA — AI Document Assistant</span>
        </div>
      </footer>
    </div>
  );
}

function SCGradient() {
  return (
    <div className="landing__aurora" aria-hidden="true">
      <span className="landing__aurora-blob landing__aurora-blob--a" />
      <span className="landing__aurora-blob landing__aurora-blob--b" />
    </div>
  );
}