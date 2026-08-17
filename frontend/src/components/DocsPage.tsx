import { useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import Reveal from "./Reveal";

/* ============================================================
   DATA
   ============================================================ */

interface NavItem {
  id: string;
  label: string;
}

const NAV: NavItem[] = [
  { id: "overview", label: "Обзор" },
  { id: "getting-started", label: "Как пользоваться" },
  { id: "features", label: "Возможности" },
  { id: "benefits", label: "Преимущества" },
  { id: "privacy", label: "Приватность" },
  { id: "roadmap", label: "Развитие" },
];

const TOC: NavItem[] = [
  { id: "overview", label: "Обзор" },
  { id: "getting-started", label: "Как пользоваться" },
  { id: "features", label: "Возможности" },
  { id: "benefits", label: "Преимущества" },
  { id: "privacy", label: "Приватность" },
  { id: "roadmap", label: "Развитие" },
];

const SECTION_IDS = TOC.map((t) => t.id);

/* ============================================================
   UTILITIES
   ============================================================ */

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

/* ============================================================
   ROADMAP
   ============================================================ */

const ROADMAP = [
  {
    status: "Сейчас",
    tone: "now" as const,
    title: "Закрытая бета",
    text: "Зарегистрированные пользователи получают доступ к работе с документами, поиску и AI-помощнику.",
  },
  {
    status: "Бета",
    tone: "soon" as const,
    title: "Расширение возможностей",
    text: "Уточнение инструментов редактирования, расширение форматов, метрики качества ответов.",
  },
  {
    status: "Запуск",
    tone: "next" as const,
    title: "Публичный запуск",
    text: "Полная документация, политики и поддержка для широкой аудитории.",
  },
  {
    status: "Будущее",
    tone: "later" as const,
    title: "Новые направления",
    text: "Направления изучаются; конкретный состав функций будет подтверждён отдельно.",
  },
];

function RoadmapBlock() {
  return (
    <div className="docs-roadmap">
      {ROADMAP.map((item, i) => (
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

/* ============================================================
   SCROLLSPY
   ============================================================ */

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

/* ============================================================
   MAIN PAGE
   ============================================================ */

export default function DocsPage({ onHome }: { onHome: () => void }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const active = useScrollspy(SECTION_IDS);

  const scrollTo = (id: string) => {
    setMenuOpen(false);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  return (
    <div className="docs">
      <header className="docs__nav">
        <div className="docs__nav-inner">
          <button type="button" className="docs__brand" onClick={onHome} title="На главную">
            ADA
            <span className="docs__brand-sub">Documentation</span>
          </button>

          <nav className={`docs__links ${menuOpen ? "docs__links--open" : ""}`}>
            {NAV.map((item) => (
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

          <button
            type="button"
            className={`docs__burger ${menuOpen ? "docs__burger--open" : ""}`}
            onClick={() => setMenuOpen((v) => !v)}
            aria-label="Меню"
            aria-expanded={menuOpen}
          >
            <i />
            <i />
          </button>
        </div>
      </header>

      <div className="docs__layout">
        <aside className={`docs__toc ${menuOpen ? "docs__toc--open" : ""}`}>
          <span className="docs__toc-title">Содержание</span>
          {TOC.map((item) => (
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
              ← На главную
            </button>
          </div>
        </aside>

        <main className="docs__content">
          <Section id="overview" kicker="Обзор" title="Ваш помощник для работы с документами">
            <p className="docs__lead">
              <strong>ADA — AI Document Assistant</strong> — это сервис, который хранит ваши документы и отвечает на
              вопросы о них на обычном языке. Не нужно листать файлы в поисках нужной строчки: задайте вопрос своим
              словами — и получите точный ответ вместе с указанием источника.
            </p>
            <p>
              Загрузите документы в личное пространство, и ADA извлечёт из них текст, поймёт содержание и будет готова
              отвечать. Спросите «сколько составляет рекламный бюджет на квартал?» или «что написано в договоре о
              расторжении?» — сервис найдёт нужные фрагменты, соберёт ответ и покажет, из каких именно файлов взята
              информация.
            </p>
            <ul className="docs__list docs__list--grid">
              <CheckItem>загрузка PDF, DOCX, ODT, TXT и Markdown</CheckItem>
              <CheckItem>ответы на вопросы о документах</CheckItem>
              <CheckItem>ответы с указанием источников</CheckItem>
              <CheckItem>поиск по смыслу и по точным словам</CheckItem>
              <CheckItem>создание новых документов</CheckItem>
              <CheckItem>редактирование с сохранением оригинала</CheckItem>
              <CheckItem>работа с несколькими документами сразу</CheckItem>
              <CheckItem>история чатов</CheckItem>
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
            kicker="Как пользоваться"
            title="Начните с четырёх простых шагов"
            sub="Весь сервис устроен так, чтобы им можно было пользоваться без подготовки."
          >
            <ul className="docs__list">
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>Зарегистрируйтесь и войдите.</strong> Создайте аккаунт на главной странице — это займёт
                  меньше минуты.
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>Загрузите документы.</strong> Перетащите файлы в боковую панель или нажмите кнопку «+» в поле
                  ввода. Поддерживаются PDF, DOCX, ODT, TXT и Markdown.
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>Задайте вопрос.</strong> Опишите своими словами, что нужно найти или узнать. ADA сама найдёт
                  релевантные фрагменты и покажет источники ответа.
                </span>
              </li>
              <li className="docs-check">
                <span className="docs-check__mark">
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="m4 12.5 5 5L20 6.5" />
                  </svg>
                </span>
                <span className="docs-check__text">
                  <strong>Попросите создать или отредактировать документ.</strong> Результат всегда сохраняется отдельно,
                  а исходный файл остаётся без изменений.
                </span>
              </li>
            </ul>
            <div className="docs__cols">
              <div className="docs__card">
                <h3>Не нужно помнить имена файлов</h3>
                <p>
                  Достаточно описать, что ищете: «шаблон договора», «данные сотрудника», «последний отчёт». ADA сама
                  найдёт подходящие документы.
                </p>
              </div>
              <div className="docs__card">
                <h3>Работа с несколькими документами</h3>
                <p>
                  Можно анализировать несколько файлов сразу: сравнить условия, найти все упоминания темы или собрать
                  информацию из разных источников в одном ответе.
                </p>
              </div>
              <div className="docs__card">
                <h3>Всё, что вы сделали, сохраняется</h3>
                <p>
                  Чаты, загруженные и созданные документы хранятся в вашей учётной записи. Можно вернуться к любому
                  разговору или скачать файл в любой момент.
                </p>
              </div>
            </div>
          </Section>

          <Section id="features" kicker="Возможности" title="Что умеет ADA">
            <div className="docs__cols">
              <div className="docs__card">
                <h3>Поиск по документам</h3>
                <p>
                  Находит информацию по смыслу и по точным словам. Даже если формулировка вопроса отличается от текста
                  документа, поиск покажет нужные фрагменты.
                </p>
              </div>
              <div className="docs__card">
                <h3>Ответы с источниками</h3>
                <p>
                  Каждый ответ сопровождается списком документов и фрагментов, на основе которых он составлен. Вы всегда
                  можете проверить, откуда взята информация.
                </p>
              </div>
              <div className="docs__card">
                <h3>Создание документов</h3>
                <p>
                  Попросите «сделай договор», «подготовь отчёт» или «составь список пунктов» — ADA создаст готовый файл
                  в формате DOCX, ODT или TXT и сохранит его в ваше пространство.
                </p>
              </div>
              <div className="docs__card">
                <h3>Редактирование документов</h3>
                <p>
                  «Сократи договор до одного абзаца», «переведи на русский», «замени дату» — изменения вносятся в копию
                  документа. Оригинал никогда не перезаписывается.
                </p>
              </div>
              <div className="docs__card">
                <h3>Чаты с памятью</h3>
                <p>
                  Диалог хранит историю и контекст. Можно уточнять вопросы и возвращаться к прошлым обсуждениям в любой
                  момент.
                </p>
              </div>
              <div className="docs__card">
                <h3>Изоляция между пользователями</h3>
                <p>
                  Ваши документы, поиск и чаты видны только вам. Чужие файлы недоступны — данные каждого пользователя
                  полностью отделены.
                </p>
              </div>
            </div>
          </Section>

          <Section id="benefits" kicker="Преимущества" title="Почему это удобно">
            <div className="docs__cols">
              <div className="docs__card">
                <h3>Экономия времени</h3>
                <p>
                  Вместо того чтобы открывать десятки файлов и искать нужную страницу вручную, вы задаёте один вопрос и
                  сразу получаете ответ с источниками.
                </p>
              </div>
              <div className="docs__card">
                <h3>Привязанность к источникам</h3>
                <p>
                  Ответы строятся на ваших документах и всегда снабжены ссылками на них. Вы видите, откуда взята каждая
                  часть ответа, и можете проверить точность.
                </p>
              </div>
              <div className="docs__card">
                <h3>Понятно без обучения</h3>
                <p>
                  Никаких сложных команд и синтаксиса: пишите вопросы обычным языком, как в переписке с коллегой.
                </p>
              </div>
              <div className="docs__card">
                <h3>Оригиналы в безопасности</h3>
                <p>
                  Редактирование всегда работает с копией. Исходный файл остаётся нетронутым и доступен для скачивания в
                  любой момент.
                </p>
              </div>
              <div className="docs__card">
                <h3>Данные под контролем</h3>
                <p>
                  Вы решаете, что загружать, а что удалять. Удаление документа полностью убирает его из системы — включая
                  индекс и фрагменты.
                </p>
              </div>
              <div className="docs__card">
                <h3>Русскоязычный интерфейс</h3>
                <p>
                  Весь интерфейс локализован на русский язык, со светлой и тёмной темами на выбор.
                </p>
              </div>
            </div>
          </Section>

          <Section id="privacy" kicker="Приватность" title="Ваши документы — только ваши">
            <div className="docs__cols">
              <div className="docs__card">
                <h3>Данные изолированы</h3>
                <p>
                  Документы, поиск и переписка одного пользователя недоступны другим. Чужие файлы даже не отображаются в
                  выдаче.
                </p>
              </div>
              <div className="docs__card">
                <h3>Пароли не хранятся открыто</h3>
                <p>
                  Пароли сохраняются только в зашифрованном виде и никогда не передаются третьим лицам.
                </p>
              </div>
              <div className="docs__card">
                <h3>Полный контроль удаления</h3>
                <p>
                  Вы можете удалить документ или чат в один клик. Удалённые данные не остаются ни в поиске, ни в
                  контексте ответов.
                </p>
              </div>
              <div className="docs__card">
                <h3>Честные ответы</h3>
                <p>
                  Если в документах нет нужной информации, сервис так и скажет, вместо того чтобы выдумывать ответ.
                </p>
              </div>
            </div>
          </Section>

          <Section id="roadmap" kicker="Развитие" title="Куда мы движемся">
            <p>Дорожная карта отражает текущий статус; состав будущих функций будет подтверждён отдельно.</p>
            <RoadmapBlock />
          </Section>

          <footer className="docs__footer">
            <div className="docs__footer-brand">
              <span className="docs__footer-logo">ADA</span>
              <p className="docs__footer-tagline">Documentation</p>
            </div>
            <nav className="docs__footer-col">
              <span className="docs__footer-heading">Навигация</span>
              <button type="button" onClick={() => scrollTo("overview")}>Обзор</button>
              <button type="button" onClick={() => scrollTo("getting-started")}>Как пользоваться</button>
              <button type="button" onClick={() => scrollTo("privacy")}>Приватность</button>
            </nav>
            <div className="docs__footer-col">
              <span className="docs__footer-heading">Код</span>
              <span className="docs__footer-note">
                GitHub: private repository — source code currently closed.
              </span>
            </div>
            <div className="docs__footer-col">
              <span className="docs__footer-heading">Контакты</span>
              <button type="button" onClick={onHome}>Contact</button>
            </div>
          </footer>
        </main>
      </div>
    </div>
  );
}