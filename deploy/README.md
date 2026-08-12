# Deploy

The project ships as two Docker images published to GHCR:

| Image | Built from | Prod |
|-------|-----------|------|
| `ghcr.io/kotyara12345678/ai-document-assistant-backend` | `backend/Dockerfile` | FastAPI + uvicorn |
| `ghcr.io/kotyara12345678/ai-document-assistant-frontend` | `frontend/Dockerfile` | nginx serving the built SPA (proxies `/api` → backend) |

Targets are plain Docker Compose hosts (no Kubernetes). Each environment runs
its own **single instance** of the full application stack behind nginx.

## Environments and entrypoints

| File | Trigger | Compose project | Public entry |
|------|---------|-----------------|--------------|
| `.github/workflows/ci.yml` | PR + push to `main` | CI stack | n/a |
| `.github/workflows/docker-publish.yml` | push to `main` / `v*` tags | n/a | GHCR |
| `.github/workflows/deploy-staging.yml` | push to `main` / manual | `ada-staging` | `:5173` |
| `.github/workflows/deploy-production.yml` | manual only | `ada-production` | `:80` |

## Pipeline flow (what happens on a push to `main`)

1. `docker-publish` — builds and pushes backend/frontend images (`main-<sha>` + `latest`).
2. `deploy-staging` — builds fresh `staging-<sha>` images, then on the staging
   server: db up → **alembic migrations** → full stack → **full HTTP E2E smoke**
   (register → upload → search → chat → agent → download a real `.docx`).
3. Production is never automatic. Run `deploy-production` from the Actions tab,
   pick an existing GHCR tag, and it runs db up → migrations → full stack →
   light smoke (`/health`, `/api/ready`). Light only, so no test data touches
   production.

If any critical step fails (migration, backend health, smoke), the workflow
fails and server logs are attached as an artifact (`*-deploy-logs`).

## Server layout (created by the workflows via scp)

```
$APP_DIR/                       # /opt/ada-staging or /opt/ada-production
├── deploy.sh                   # scripts/deploy/deploy.sh  (entry point)
├── docker-compose.<env>.yml    # deploy/docker-compose.staging|production.yml
├── backend/.env                # generated from GitHub Environment secrets
└── scripts/run_backend_migrations.sh
```

`backend/.env` holds the app settings — never commit a real one to the repo.

## Manual commands on a server

From the app dir. `-p` must match the environment:

```bash
# start ONLY the database (e.g. for a fresh clone or during backend swap)
docker compose -p ada-production -f docker-compose.production.yml up -d db

# run migrations
COMPOSE_PROJECT=ada-production COMPOSE_FILE=docker-compose.production.yml \
  ./scripts/run_backend_migrations.sh

# full stack
docker compose -p ada-production -f docker-compose.production.yml up -d

# watch / inspect
docker compose -p ada-production ps
docker compose -p ada-production -f docker-compose.production.yml logs -f

# stop everything (data volumes are preserved)
docker compose -p ada-production -f docker-compose.production.yml down

# full teardown INCLUDING data (dangerous)
docker compose -p ada-production -f docker-compose.production.yml down -v
```

## GitHub configuration

Workflows read deployment config from GitHub **Environment secrets/vars** for
`staging` and `production`.

Secrets (required unless noted):
- `DEPLOY_HOST` — server IP/DNS
- `DEPLOY_USER` — SSH user
- `DEPLOY_SSH_KEY` — private key (public key must be in server `~/.ssh/authorized_keys`)
- `JWT_SECRET`, `GIGACHAT_CLIENT_ID`, `GIGACHAT_CLIENT_SECRET`
- Optional: `DEPLOY_PORT` (SSH port, default `22`), `QDRANT_API_KEY`,
  `GIGACHAT_SCOPE`, `GIGACHAT_BASE_URL`, `GIGACHAT_AUTH_URL`, `GIGACHAT_MODEL`

Vars (optional):
- `DEPLOY_APP_DIR` (default `/opt/ada-staging` / `/opt/ada-production`)
- `DEPLOY_BACKEND_PORT`, `DEPLOY_FRONTEND_PORT` (defaults: staging `8000`/`5173`, production `8000`/`80`)
- `DEPLOY_PUBLIC_URL` — if the app is fronted by an external reverse proxy,
  set this to the public URL used for the post-deploy smoke check
- `CORS_ORIGINS`, `ADMIN_EMAILS`

## Rollback

Images keep immutable tags (`main-<sha>`, `vX.Y.Z`). To roll back, re-run the
deploy workflow with the previous tag, or on the server:

```bash
IMAGE_BACKEND=ghcr.io/kotyara12345678/ai-document-assistant-backend:<previous> \
IMAGE_FRONTEND=ghcr.io/kotyara12345678/ai-document-assistant-frontend:<previous> \
  docker compose -p ada-production -f docker-compose.production.yml up -d
```

## Notes

- Staging DB binds to `127.0.0.1:55432`, production DB to `127.0.0.1:65432`,
  so both can coexist on one host without port clashes. Qdrant binds locally too.
- Data stores are not publicly reachable: production backend binds to `127.0.0.1`
  and is only reachable through the nginx frontend container on `:80`.
- `deploy.sh` never stores secrets on the runner — the `.env` file is
  generated in the workflow only, from Environment secrets.