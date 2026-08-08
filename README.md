# AI Document Assistant

Веб-сервис: пользователь загружает документы (PDF/DOCX), система индексирует их,
а затем позволяет задавать вопросы по содержимому через AI (RAG).

> Текущий статус: **фундамент MVP**. Загрузка файлов, хранение и базовые API
> работают. Извлечение текста, векторизация и генерация ответов — следующие этапы.

## Архитектура

```
┌────────────┐     HTTP     ┌─────────────────────────────────────────────┐
│  Frontend  │ ───────────▶ │                   Backend (FastAPI)         │
└────────────┘              │  app/main.py → api/routes → services        │
                            │       │                        │            │
                            │       ▼                        ▼            │
                            │  database/session.py     vector/client.py   │
                            └───────┬────────────────────────┬────────────┘
                                    │                        │
                                    ▼                        ▼
                              ┌───────────┐           ┌──────────┐
                              │ PostgreSQL │           │  Qdrant  │
                              │ (метаданные│           │ (векторы)│
                              │  документов│           └──────────┘
                              └───────────┘
```

### Слои приложения (`backend/app/`)

| Директория   | Назначение                                                       |
| ------------ | ---------------------------------------------------------------- |
| `api/routes` | HTTP-эндпоинты (тонкие, только валидация + вызов сервисов)       |
| `core`       | Конфигурация (`config.py`) и безопасность (`security.py`)        |
| `database`   | Движок/Session (SQLAlchemy 2.0) и базовый класс моделей          |
| `models`     | ORM-модели: `User`, `Document`, `UsageLog`                       |
| `schemas`    | Pydantic-схемы (запросы/ответы API)                              |
| `services`   | Бизнес-логика (загрузка, чат)                                    |
| `vector`     | Клиент Qdrant и управление коллекцией                            |

Миграции БД — [Alembic](backend/alembic). Пароли хэшируются через bcrypt
(`core/security.py`). Аутентификация ещё не реализована — эндпоинты используют
заглушку `get_current_user_id()`.

## Быстрый старт

Требования: Docker + Docker Compose.

```bash
# 1. Подготовить конфигурацию
cp backend/.env.example backend/.env

# 2. Собрать и запустить (PostgreSQL + Qdrant + backend)
docker compose up --build

# 3. Применить миграции (в отдельном терминале)
docker compose exec backend alembic upgrade head

# 4. Проверить
curl http://localhost:8000/health
```

Интерфейс API (Swagger): http://localhost:8000/docs

### Локальная разработка без Docker

```bash
cd backend
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
# задать DATABASE_URL на свой PostgreSQL и QDRANT_URL
uvicorn app.main:app --reload
```

## API

| Метод | Путь                   | Описание                        |
| ----- | ---------------------- | ------------------------------- |
| GET   | `/health`              | Статус сервиса и зависимостей   |
| POST  | `/api/documents/upload`| Загрузка PDF/DOCX               |
| GET   | `/api/documents`       | Список документов пользователя  |
| POST  | `/api/chat`            | Вопрос по документам (заглушка) |

Пример запроса на загрузку:

```bash
curl -F "file=@report.pdf" http://localhost:8000/api/documents/upload
```

Пример чата:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"document_id": 1, "question": "Какой бюджет на маркетинг?"}'
```

## Следующие этапы

1. **Извлечение текста** — PDF (pdfplumber/pypdf) и DOCX (python-docx),
   OCR для сканов (Tesseract).
2. **Chunking** — разбивка на смысловые части с сохранением номера страницы.
3. **Embeddings** — генерация векторов (Gemini/Qwen API) и загрузка в Qdrant.
4. **Поиск** — семантический поиск фрагментов по вопросу (Qdrant search).
5. **Генерация ответов** — LLM-промпт по найденным фрагментам + источники.
6. **Аутентификация** — JWT (замена `get_current_user_id`).
7. **Frontend** — простая веб-версия (`frontend/`), затем опционально Electron.
8. **Учёт токенов** — запись `UsageLog` на каждый запрос к LLM.
