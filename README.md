# AI Document Assistant (ADA)

**AI Document Assistant (ADA)** — веб-сервис для работы с пользовательскими документами через RAG и AI-агента.

Пользователь загружает документы, задаёт вопросы естественным языком, ищет информацию по содержимому и получает ответы или структурированные результаты на основе найденных фрагментов.

> **Статус:** рабочий MVP, CI/CD подготовлен для staging и production.

## Возможности

- регистрация и JWT-аутентификация;
- bcrypt-хеширование паролей;
- приватная изоляция данных пользователей;
- PDF, DOCX, TXT, MD и ODT;
- извлечение текста и автоматический chunking;
- embeddings через MiniLM;
- semantic search через Qdrant;
- keyword search через PostgreSQL FTS;
- hybrid search;
- CrossEncoder reranking;
- RAG-чат с GigaChat;
- AI-агент, самостоятельно выбирающий стратегию работы с документами;
- metadata-aware retrieval;
- ответы с источниками;
- просмотр найденных фрагментов;
- приватная история чатов;
- генерация структурированных результатов на основе документов;
- русский интерфейс, светлая/тёмная тема;
- Docker Compose;
- backend/frontend tests и HTTP E2E;
- автоматический staging deploy;
- ручной production deploy по версии.

## Архитектура

```text
                         ┌──────────────────────┐
                         │      Frontend        │
                         │ React + Vite + TS    │
                         └──────────┬───────────┘
                                    │ HTTP
                                    ▼
                         ┌──────────────────────┐
                         │   FastAPI Backend    │
                         │ Auth / Documents     │
                         │ Chat / Agent         │
                         │ Retrieval / Reranker │
                         └───────┬───────┬──────┘
                                 │       │
                    ┌────────────┘       └────────────┐
                    ▼                                 ▼
             ┌──────────────┐                   ┌──────────────┐
             │ PostgreSQL   │                   │   Qdrant     │
             │ users        │                   │ embeddings   │
             │ documents    │                   │ vectors      │
             │ chunks       │                   │ payloads     │
             │ chats / FTS  │                   └──────────────┘
             └──────────────┘

                       Retrieval Layer
                              │
                 ┌────────────┴────────────┐
                 ▼                         ▼
          Semantic Search            Keyword Search
             Qdrant                  PostgreSQL FTS
                 │                         │
                 └────────────┬────────────┘
                              ▼
                        Hybrid Merge
                              │
                              ▼
                        CrossEncoder
                          Reranker
                              │
                             Top-K
                              │
                              ▼
                         AI Agent / RAG
                              │
                              ▼
                           GigaChat
                              │
                              ▼
                        Answer + Sources
```

## AI Agent

ADA не требует от пользователя специальной команды вроде «найди в документе». Агент сам определяет, зависит ли задача от загруженных документов и какие действия нужны.

```text
User query
    │
    ▼
Agent decision
    │
    ├── обычный ответ ───────────────► LLM
    │
    └── нужен контекст документов
                │
                ▼
          Retrieval strategy
                │
                ├── semantic search
                ├── keyword search
                ├── metadata filter
                └── additional retrieval
                │
                ▼
             Reranking
                │
                ▼
          Relevant context
                │
                ▼
             GigaChat
                │
                ▼
              Result
```

Для сложных запросов агент может выполнять дополнительные retrieval-шаги. Если информации недостаточно, он не должен её выдумывать.

### Генерация документов

Агент может использовать найденные данные для создания структурированного результата: договора, отчёта, инструкции и т. п.

```text
User request
     ↓
Agent
     ↓
Retrieval
     ↓
Relevant chunks
     ↓
GigaChat
     ↓
Generated document
```

Если результатом является копируемый документ, frontend может отображать **только содержимое документа** в отдельном моноширинном блоке и предоставить кнопку копирования. Служебные рассуждения и retrieval-данные в документ не попадают.

## Hybrid Search

### Semantic search

MiniLM преобразует запрос в embedding, после чего Qdrant выполняет vector similarity search. Это позволяет находить смыслово близкие фрагменты даже при другой формулировке.

### Keyword search

PostgreSQL Full-Text Search используется для точных совпадений: имён, кодов, артикулов, терминов и числовых значений.

### Merge + reranking

```text
Query
 │
 ├──► MiniLM → Qdrant ──────┐
 │                          │
 └──► PostgreSQL FTS ───────┤
                            ▼
                       Hybrid Merge
                            │
                       Candidates
                            │
                            ▼
                       CrossEncoder
                         Reranker
                            │
                           Top-K
                            │
                            ▼
                         GigaChat
```

Дубликаты определяются по `(document_id, chunk_index)`. При ошибке или отключении reranker используется graceful fallback на hybrid retrieval.

## Retrieval quality

На тестовом наборе из 8 вопросов и 8 документов:

| Метрика | Без reranker | С reranker |
|---|---:|---:|
| MRR@5 | 0.938 | **1.000** |
| Recall@5 | 1.000 | **1.000** |

Для сравнения используется `backend/scripts/compare_reranker.py`.

## RAG

LLM получает только наиболее релевантный контекст:

```text
User question
      +
Relevant chunks
      ↓
   GigaChat
      ↓
Answer + Sources
```

Если релевантные фрагменты не найдены, система не должна выдавать выдуманный ответ от имени документов.

## Metadata-aware retrieval

Metadata не добавляются в каждый запрос автоматически. Система может определить, нужны ли они конкретному запросу.

```text
«Что написано в документе?»
        ↓
только текст chunks
```

```text
«Когда загружен документ?»
        ↓
только необходимые metadata
```

```text
«Ищи только в report.pdf»
        ↓
retrieval ограничивается документом
```

Доступные поля могут включать `original_filename`, `file_type`, `file_size`, `content_length` и `created_at`. Отсутствующие поля никогда не выдумываются.

Классификатор можно отключить:

```text
CHAT_METADATA_CLASSIFIER_ENABLED=false
```

При ошибке классификации выполняется безопасный fallback без metadata.

## Documents pipeline

Поддерживаются PDF, DOCX, TXT, MD и ODT.

```text
File
 ↓
Validation
 ↓
Text extraction
 ↓
PostgreSQL
 ↓
Chunking
 ↓
MiniLM embeddings
 ↓
Qdrant
```

Исходные файлы сохраняются в Docker volume. PostgreSQL хранит пользователей, документы, chunks, metadata и чаты. Qdrant хранит vector representations.

## Authentication & privacy

Каждый пользователь имеет изолированные документы, chunks, поисковые данные, чаты и сообщения.

Защищённые API-запросы используют:

```text
Authorization: Bearer <JWT>
```

Основные endpoints:

```text
POST /api/auth/register
POST /api/auth/login
GET  /api/auth/me
```

Пароли хранятся только в виде bcrypt-хеша.

Ключевое требование: пользователь A не должен иметь возможности получить документы или чаты пользователя B.

## API

| Method | Endpoint | Назначение |
|---|---|---|
| GET | `/health` | Health check |
| GET | `/api/ready` | Readiness check |
| POST | `/api/auth/register` | Регистрация |
| POST | `/api/auth/login` | Авторизация |
| GET | `/api/auth/me` | Текущий пользователь |
| POST | `/api/documents/upload` | Загрузка документа |
| GET | `/api/documents` | Документы пользователя |
| GET | `/api/documents/{id}/content` | Просмотр текста |
| POST | `/api/documents/{id}/index` | Индексация |
| DELETE | `/api/documents/{id}` | Удаление документа |
| DELETE | `/api/documents` | Удаление всех документов |
| POST | `/api/search` | Hybrid search |
| POST | `/api/chat` | RAG-вопрос |
| GET | `/api/chats` | История чатов |
| POST | `/api/chats` | Создание чата |
| GET | `/api/chats/{id}/messages` | Сообщения |
| DELETE | `/api/chats/{id}` | Удаление чата |
| GET | `/api/admin/stats` | Admin statistics |

## Tech Stack

### Backend

- Python
- FastAPI
- SQLAlchemy 2.0
- PostgreSQL
- Alembic
- Qdrant
- PyJWT
- bcrypt
- sentence-transformers

### Frontend

- React
- Vite
- TypeScript
- Vitest

### AI / Retrieval

- **MiniLM (`all-MiniLM-L6-v2`)** — embeddings;
- **Qdrant** — vector search;
- **PostgreSQL FTS** — keyword retrieval;
- **CrossEncoder** — reranking;
- **GigaChat** — generation.

### Infrastructure

- Docker
- Docker Compose
- Nginx
- GitHub Actions
- GitHub Container Registry (GHCR)

## CI/CD

CI/CD реализован через GitHub Actions.

### `ci.yml`

Запускается на PR и push в `main`.

```text
lint
  ↓
backend tests
  ↓
frontend build + tests
  ↓
full E2E smoke
```

Backend проверяется с реальными PostgreSQL и Qdrant. GigaChat в CI заменяется mock-сервисом.

E2E проверяет полный HTTP-сценарий:

```text
health → register → upload → search → chat → agent → generated .docx
```

### `docker-publish.yml`

Images публикуются в GHCR.

Для `main`:

```text
main-<sha>
latest
```

Для release tags:

```text
vX.Y.Z
latest
```

### `deploy-staging.yml`

Автоматический deploy из `main`:

```text
main
 ↓
staging image
 ↓
SSH
 ↓
DB + migrations
 ↓
staging stack
 ↓
full E2E smoke
```

При ошибке сохраняются логи workflow.

### `deploy-production.yml`

Production deploy запускается вручную через `workflow_dispatch` с выбором конкретного version tag.

Перед deploy проверяется image в GHCR. В production выполняются только лёгкие health/readiness smoke checks; тестовые данные не создаются.

## Deployment

```text
deploy/
├── docker-compose.staging.yml
├── docker-compose.production.yml
├── scripts/
│   └── run_backend_migrations.sh
└── README.md
```

Staging и production используют отдельные Compose-конфигурации. Production frontend работает через Nginx, внутренние сервисы не должны быть публично доступны без необходимости.

Перед production deployment необходимо настроить secrets, persistent storage и backups.

## Testing

Последняя CI-валидация:

```text
Backend: 163 passed
Coverage: 89%
Frontend: 17/17
E2E: ALL CHECKS PASSED
```

Проверяются:

- YAML;
- GitHub Actions (`actionlint`);
- Docker Compose configuration;
- backend unit/integration tests;
- PostgreSQL;
- Qdrant;
- frontend build;
- frontend tests;
- полный E2E HTTP smoke;
- agent endpoint;
- GigaChat mock integration.

## Local development

### Requirements

- Docker Desktop / Docker Engine
- Docker Compose
- Node.js — если frontend запускается отдельно
- Python — если backend запускается отдельно

### Start

```bash
docker compose up --build
```

Frontend:

```text
http://localhost:5173
```

Backend health:

```text
http://localhost:8000/health
```

Swagger:

```text
http://localhost:8000/docs
```

Migrations:

```bash
docker compose exec backend alembic upgrade head
```

### Stop

```bash
docker compose down
```

Для сохранения данных используются persistent Docker volumes.

## Configuration & secrets

Секреты не должны храниться в Git.

Не коммитьте реальные:

- GigaChat credentials;
- JWT secrets;
- database passwords;
- API keys;
- production credentials.

Production environment values передаются через GitHub Actions Environment secrets и deployment configuration.

## Security checklist

Перед production deployment необходимо проверить:

1. изоляцию данных по `user_id`;
2. JWT authentication;
3. безопасное хранение паролей;
4. отсутствие секретов в Git;
5. HTTPS;
6. PostgreSQL backups;
7. сохранность Qdrant data;
8. закрытые внутренние порты PostgreSQL/Qdrant;
9. ограничения размера файлов;
10. отсутствие hallucinated document facts при пустом retrieval.

## Roadmap

- production deployment;
- улучшение agent decision loop;
- более устойчивый multi-step retrieval;
- улучшение document generation;
- production logging и observability;
- оптимизация стоимости LLM/API;
- улучшение обработки сложных PDF;
- дополнительные форматы экспорта;
- расширение B2C/B2B сценариев;
- усиление security и backup strategy.

## Philosophy

> **Пользователь не должен думать о том, как искать информацию. Он должен просто спросить AI, а система сама должна выбрать подходящий способ работы с его документами.**

Retrieval, reranking, metadata и agent orchestration остаются внутренними механизмами системы, а пользовательский интерфейс должен оставаться максимально простым.
