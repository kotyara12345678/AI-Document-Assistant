# AI Document Assistant

Веб-сервис: пользователь регистрируется, загружает документы (PDF/DOCX/TXT),
система индексирует их, а затем позволяет задавать вопросы по содержимому
через AI (RAG) — ответы формируются по найденным фрагментам документов с
указанием источников.

> Текущий статус: **рабочее ядро MVP**. Есть регистрация/вход (JWT, bcrypt),
> загрузка и индексация файлов, семантический поиск и RAG-чат с ответами
> GigaChat и ссылками на исходные фрагменты. Следующие этапы: OCR для сканов,
> учёт токенов LLM и т.д. (см. конец файла).

## Архитектура

```
┌────────────┐     HTTP     ┌─────────────────────────────────────────────┐
│  Frontend  │ ───────────▶ │              Backend (FastAPI)              │
│  (React)   │              │  app/main.py → api/routes → services        │
└────────────┘              │       │                        │            │
                            │       ▼                        ▼            │
                            │  database/session.py     vector/client.py   │
                            └───────┬────────────────────────┬────────────┘
                                    │                        │
                                    ▼                        ▼
                              ┌───────────┐            ┌──────────┐
                              │ PostgreSQL │            │  Qdrant  │
                              │ (user,     │            │ (векторы │
                              │  docs,     │            │  чанков) │
                              │  chat)     │            └──────────┘
                              └───────────┘
```

### Слои приложения (`backend/app/`)

| Директория   | Назначение                                                        |
| ------------ | ----------------------------------------------------------------- |
| `api/routes` | HTTP-эндпоинты: `auth`, `documents`, `chat`, `chats`, `search`    |
| `core`       | Конфигурация (`config.py`) и безопасность (`security.py`, JWT)    |
| `database`   | Движок/Session (SQLAlchemy 2.0) и базовый класс моделей           |
| `models`     | ORM-модели: `User`, `Document`, `DocumentChunk`, `Chat`, …        |
| `schemas`    | Pydantic-схемы (запросы/ответы API)                               |
| `services`   | Бизнес-логика: индексация, retrieval/rerank, генерация (GigaChat) |
| `vector`     | Клиент Qdrant и управление коллекцией                             |
| `scripts`    | Утилиты (например, разовые скрипты)                               |

Миграции БД — [Alembic](backend/alembic). Аутентификация: JWT (HS256,
`core/security.py`), короткий токен на 7 дней (настраивается через
`JWT_EXPIRE_MINUTES`). Пароли хэшируются через bcrypt. Все эндпоинты
кроме `/health` и `/api/auth/register`/`/api/auth/login` требуют заголовок
`Authorization: Bearer <token>`.

Фронтенд — `frontend/` (React + Vite + TypeScript). Перед первым использованием
показывается экран входа/регистрации; после входа JWT хранится в
`localStorage` и подставляется во все API-запросы.

## Быстрый старт

Требования: Docker + Docker Compose.

```bash
# 1. Подготовить конфигурацию
cp backend/.env.example backend/.env

# 2. Собрать и запустить (PostgreSQL + Qdrant + backend + frontend)
docker compose up --build

# 3. Применить миграции (в отдельном терминале)
docker compose exec backend alembic upgrade head
```

Проверка:

```bash
curl http://localhost:8000/health
```

- Фронтенд: http://localhost:5173
- API-документация (Swagger): http://localhost:8000/docs

### Локальная разработка без Docker

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt -r requirements-dev.txt
# задать DATABASE_URL на свой PostgreSQL и QDRANT_URL на Qdrant
uvicorn app.main:app --reload
```

Для фронтенда: `cd frontend && npm install && npm run dev` (Vite проксирует
`/api` на backend). Продакшен-сборка — `npm run build`.

## Аутентификация

Регистрация и вход возвращают `access_token` (JWT) + профиль пользователя:

```bash
# Регистрация (email нормализуется в нижний регистр)
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123","password_confirm":"secret123"}'

# Вход
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"user@example.com","password":"secret123"}'

# Далее токен подставляется во все запросы
TOKEN="<access_token>"
curl http://localhost:8000/api/auth/me -H "Authorization: Bearer $TOKEN"
```

## API

| Метод  | Путь                            | Описание                           |
| ------ | ------------------------------- | ---------------------------------- |
| GET    | `/health`                       | Статус сервиса и зависимостей      |
| POST   | `/api/auth/register`            | Регистрация → JWT                  |
| POST   | `/api/auth/login`               | Вход → JWT                         |
| GET    | `/api/auth/me`                  | Текущий пользователь               |
| POST   | `/api/documents/upload`         | Загрузка PDF/TXT/DOCX + индексация |
| GET    | `/api/documents`                | Список документов пользователя     |
| GET    | `/api/documents/{id}/content`   | Извлечённый текст документа        |
| POST   | `/api/documents/{id}/index`     | Повторная индексация документа     |
| DELETE | `/api/documents/{id}`           | Удаление документа и его чанков    |
| DELETE | `/api/documents`                | Очистить все документы пользователя|
| POST   | `/api/search`                   | Семантический поиск по фрагментам  |
| POST   | `/api/chat`                     | Вопрос по документам → ответ+источники |
| GET    | `/api/chats`                    | Список чатов                        |
| POST   | `/api/chats`                    | Создать чат                         |
| GET    | `/api/chats/{chat_id}/messages`| Сообщения чата                      |
| DELETE | `/api/chats/{chat_id}`          | Удалить чат                         |

Все эндпоинты, кроме `/health` и регистрации/входа, являются приватными и
разделяют данные между пользователями: каждый видит только свои документы,
чаты и результаты поиска.

При загрузке сервис проверяет расширение и содержимое (magic bytes),
сохраняет исходный файл в volume `/data/uploads`, извлекает полный текст и
сохраняет метаданные + текст в PostgreSQL. Затем текст автоматически
разбивается на чанки, эмбеддится MiniLM (`all-MiniLM-L6-v2`) и индексируется
в Qdrant. Ошибки индексации не ломают запись Document (логируются).
Текст документа не возвращается в ответе.

Пример загрузки:

```bash
TOKEN="<JWT от /api/auth/login>"
curl -F "file=@report.pdf" http://localhost:8000/api/documents/upload \
  -H "Authorization: Bearer $TOKEN"
```

Ответ (текст документа не отдаётся):

```json
{
  "id": 1,
  "filename": "79bab8b682ec424cb6469f85144bf29c.pdf",
  "original_filename": "report.pdf",
  "file_type": "pdf",
  "file_size": 123456,
  "content_length": 54231,
  "created_at": "2026-08-08T08:52:56Z"
}
```

Семантический поиск:

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "какой бюджет на маркетинг", "limit": 5}'
```

Ответ — найденные чанки с `document_id`, `filename`, `chunk_index`,
`text` и `score`. Индексация Qdrant пересоздаётся через
`POST /api/documents/{id}/index` (сначала векторы удаляются, затем
индексируются заново).

Чат по документам:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"chat_id": 1, "question": "Какой бюджет на маркетинг?"}'
```

Ответ включает `answer` (сгенерирован через GigaChat) и `sources` — ссылки
`document_id` / `filename` / `chunk_index` / `score`, по которым фронтенд
открывает фрагмент в просмотрщике документа. Если релевантных фрагментов нет,
вернётся честный ответ без вызова LLM и с пустым `sources`.

Проверка Qdrant вручную:

```bash
curl http://localhost:6333/collections/document_chunks   # status, points_count, vector_size=384
curl -X POST http://localhost:6333/collections/document_chunks/points/scroll \
  -H "Content-Type: application/json" -d '{"limit": 5, "with_vector": false}'
```

## Фронтенд

- Экран **входа / регистрации** — одна карточка с переключателем
  «Вход / Регистрация», показ пароля, инлайн-ошибки.
- После входа — чат слева (список чатов + документы + зона загрузки),
  основное поле для вопросов, ответы с источниками и просмотрщик файлов
  с подсветкой найденных фрагментов.
- Кнопка «Выйти» в сайдбаре; JWT живёт в `localStorage`, при 401 токен
  сбрасывается и показывается экран входа.
- Светлая/тёмная тема (переключатель в сайдбаре).

## Тесты

Backend (`backend/tests`): pytest против живого стека (PostgreSQL + Qdrant):

```bash
docker compose exec backend pytest tests -q
```

Проверяется: регистрация/вход/`/me`, полная изоляция данных между двумя
пользователями (документы, поиск, чаты, источники), отклонение невалидных
файлов, загрузка TXT/PDF/DOCX, появление Document в PostgreSQL и чанков в
Qdrant, семантический поиск, (пере)индексация, удаление векторов,
E2E-сценарий загрузка после логина.

Frontend (`frontend/`): vitest + testing-library:

```bash
cd frontend
npm install
npm test
```

Обычно — регрессионный тест на то, что загрузка документа после входа
передаёт `Authorization: Bearer <token>`.

## Следующие этапы

1. **OCR** — извлечение текста из сканов в PDF (Tesseract).
2. **Учёт токенов** — запись `UsageLog` на каждый запрос к LLM.
3. **SSO / проверка токенов** — `refresh`-токены и ротация секрета.
4. **Админка** — управление пользователями, лимиты.
5. **Улучшение RAG** — гибридный поиск (BM25 + векторы), переиспользование
   эмбеддингов модели.
