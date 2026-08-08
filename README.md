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
                              ┌───────────┐            ┌──────────┐
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

| Метод | Путь                   | Описание                               |
| ----- | ---------------------- | -------------------------------------- |
| GET   | `/health`              | Статус сервиса и зависимостей          |
| POST  | `/api/documents/upload`| Загрузка PDF/TXT/DOCX + индексация     |
| GET   | `/api/documents`       | Список документов пользователя         |
| POST  | `/api/documents/{id}/index` | Повторная индексация документа     |
| POST  | `/api/search`          | Семантический поиск по фрагментам      |
| POST  | `/api/chat`            | Вопрос по документам (заглушка)        |

При загрузке сервис проверяет расширение и содержимое (magic bytes),
сохраняет исходный файл в volume `/data/uploads`, извлекает полный текст и
сохраняет метаданные + текст в PostgreSQL. Затем текст автоматически
разбивается на чанки, эмбеддится MiniLM (`all-MiniLM-L6-v2`) и индексируется
в Qdrant. Ошибки индексации не ломают запись Document (логируются).
Текст документа не возвращается в ответе.

Пример запроса на загрузку:

```bash
curl -F "file=@report.pdf" http://localhost:8000/api/documents/upload
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
  -H "Content-Type: application/json" \
  -d '{"query": "какой бюджет на маркетинг", "limit": 5}'
```

Ответ — найденные чанки с `document_id`, `filename`, `chunk_index`,
`text` и `score`. Индексация Qdrant автоматически удаляется/повторяется через
`POST /api/documents/{id}/index` (сначала векторы удаляются, затем
индексируются заново).

Проверка Qdrant вручную:

```bash
curl http://localhost:6333/collections/document_chunks   # status, points_count, vector_size=384
curl -X POST http://localhost:6333/collections/document_chunks/points/scroll \
  -H "Content-Type: application/json" -d '{"limit": 5, "with_vector": false}'
```

> Аутентификация пока не реализована: эндпоинты используют заглушку
> `get_current_user_id()` (user_id=1). Чтобы загрузка работала, в БД должен
> существовать пользователь с id=1:
> `docker compose exec db psql -U docassistant -d docassistant -c "INSERT INTO users (email, password_hash) VALUES ('demo@example.com', 'demo');"`

Пример чата:

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"document_id": 1, "question": "Какой бюджет на маркетинг?"}'
```

## Тесты

```bash
# dev-зависимости ставятся один раз
docker compose exec -u root backend pip install -r requirements-dev.txt
docker compose exec backend pytest tests -q
```

Тесты проверяют: загрузку TXT/PDF/DOCX (201), отклонение битых/пустых
файлов, появление Document в PostgreSQL, появление чанков в Qdrant,
semantic search и соответствие найденного чанка нужному document_id,
ручную (пере)индексацию и удаление векторов.

## Следующие этапы

1. **Генерация ответов** — LLM-промпт по найденным фрагментам + источники.
2. **OCR** — извлечение текста из сканов PDF (Tesseract).
3. **Аутентификация** — JWT (замена `get_current_user_id`).
4. **Frontend** — простая веб-версия (`frontend/`), затем опционально Electron.
5. **Учёт токенов** — запись `UsageLog` на каждый запрос к LLM.
