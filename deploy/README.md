# Deploy

The project ships as two Docker images published to GHCR:

| Image | Built from | Prod |
|-------|-----------|------|
| `ghcr.io/kotyara12345678/ai-document-assistant-backend` | `backend/Dockerfile` | FastAPI + uvicorn |
| `ghcr.io/kotyara12345678/ai-document-assistant-frontend` | `frontend/Dockerfile.prod` | nginx serving the built SPA (proxies `/api`, `/api2`, `/health` → backend; TLS on `:443` when certs are mounted) |

Targets are plain Docker Compose hosts (no Kubernetes). Each environment runs
its own **single instance** of the full application stack behind nginx.

## Environments and entrypoints

| File | Trigger | Compose project | Public entry |
|------|---------|-----------------|--------------|
| `.github/workflows/ci.yml` | PR + push to `main` | CI stack | n/a |
| `.github/workflows/docker-publish.yml` | push to `main` / `v*` tags | n/a | GHCR |
| `.github/workflows/deploy-staging.yml` | push to `main` / manual | `ada-staging` | `:5173` |
| `.github/workflows/deploy-production.yml` | auto after Docker publish (push to `main`) / manual | `ada-production` | `:80` |

## Pipeline flow (what happens on a push to `main`)

1. `docker-publish` — builds and pushes backend/frontend images (`main-<sha>` + `latest`).
2. `deploy-staging` — builds fresh `staging-<sha>` images, then on the staging
   server: db up → **alembic migrations** → full stack → **full HTTP E2E smoke**
   (register → upload → search → chat → agent → download a real `.docx`).
3. `deploy-production` — runs **automatically** once `docker-publish` has
   finished, deploying the same `main-<sha>` images to the production VPS:
   db up → migrations → full stack → light smoke (`/health`, `/api/ready`).
   Light only, so no test data ever touches production.
4. Manual deploys are still possible from the Actions tab: run
   `deploy-production` with any existing GHCR tag (rollback, hotfix).

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
  and is only reachable through the nginx frontend container on `:80` / `:443`.

## TLS (Let's Encrypt) for production

The production frontend container listens on `:80` and `:443`. HTTPS is enabled
**automatically at container start** when a certificate pair exists in
`$APP_DIR/certs/{fullchain.pem,privkey.pem}` (bind-mounted as `:ro` to
`/etc/nginx/certs`); otherwise it serves plain HTTP (useful during first setup
and for staging, which never mounts certs). `frontend/docker-entrypoint.d/`
generates the `:443` server block and an HTTP→HTTPS redirect, keeping
`/.well-known/acme-challenge/` on plain HTTP for renewals.

One-time setup on the server (after the stack is up on `:80`, e.g. the first
auto-deploy):

```bash
apt-get update && apt-get install -y certbot

# HTTP-01 validation is served by the frontend container from this dir
# (bind-mounted to /usr/share/nginx/html/certbot).
mkdir -p /opt/ada-production/certbot-webroot

certbot certonly --webroot \
  -w /opt/ada-production/certbot-webroot \
  -d ada.env.pm \
  --agree-tos --no-eff-email -m savva.toch@mail.com

# Drop certificates where the container picks them up, then restart it.
cp /etc/letsencrypt/live/ada.env.pm/fullchain.pem /opt/ada-production/certs/fullchain.pem
cp /etc/letsencrypt/live/ada.env.pm/privkey.pem  /opt/ada-production/certs/privkey.pem
chmod 644 /opt/ada-production/certs/fullchain.pem
chmod 600 /opt/ada-production/certs/privkey.pem
docker compose -p ada-production \
  -f /opt/ada-production/docker-compose.production.yml \
  up -d --force-recreate frontend
```

Renewal (weekly cron; install as root with `crontab -e`):

```bash
0 3 * * 1 nice certbot renew --quiet --deploy-hook \
  "cp /etc/letsencrypt/live/ada.env.pm/fullchain.pem /opt/ada-production/certs/fullchain.pem && \
   cp /etc/letsencrypt/live/ada.env.pm/privkey.pem /opt/ada-production/certs/privkey.pem && \
   chmod 644 /opt/ada-production/certs/fullchain.pem && chmod 600 /opt/ada-production/certs/privkey.pem && \
   docker compose -p ada-production -f /opt/ada-production/docker-compose.production.yml restart frontend"
```

Notes:
- `deploy.sh` and the compose file create `$APP_DIR/certs` and
  `certbot-webroot` automatically; you do not need to create them by hand.
- Port `443` must be open in the cloud/hosting firewall.
- The post-deploy smoke in `deploy-production.yml` checks the public
  `DEPLOY_PUBLIC_URL` (e.g. `https://ada.env.pm`); set that var so the smoke
  goes through the real HTTPS entry point.
- `deploy.sh` never stores secrets on the runner — the `.env` file is
  generated in the workflow only, from Environment secrets.