#!/usr/bin/env bash
#
# Remote deployment script, executed ON the target server by the
# deploy-staging / deploy-production workflows.
#
# What it does (assumes a plain Docker Compose host, no Kubernetes):
#   1. pull the pinned images from GHCR
#   2. start the database and wait until it is healthy
#   3. run Alembic migrations once
#   4. start the whole stack (backend -> frontend)
#   5. wait until the backend container is healthy (readiness == /api/ready)
#
# The workflow uploads, next to this script: docker-compose.yml and
# backend/.env (generated from GitHub Environment secrets — never stored in
# the repository).
#
# Usage:
#   APP_DIR=/opt/ada COMPOSE_PROJECT=ada-staging ./deploy.sh
#
set -euo pipefail

APP_DIR="${APP_DIR:?APP_DIR is required (absolute path on the server)}"
COMPOSE_PROJECT="${COMPOSE_PROJECT:-ada}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.yml}"

cd "${APP_DIR}"

compose() {
    docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" "$@"
}

wait_container_healthy() {
    local name="$1"
    local attempts=120
    for _ in $(seq 1 "${attempts}"); do
        local status
        status=$(docker inspect -f '{{.State.Health.Status}}' "${name}" 2>/dev/null || echo starting)
        if [ "${status}" = "healthy" ]; then
            return 0
        fi
        if [ "${status}" = "unhealthy" ]; then
            echo "ERROR: container ${name} became unhealthy"
            docker logs --tail 100 "${name}" || true
            return 1
        fi
        sleep 2
    done
    echo "ERROR: container ${name} did not become healthy in time"
    docker logs --tail 100 "${name}" || true
    return 1
}

echo "[deploy] pulling images"
compose pull

echo "[deploy] starting database"
compose up -d db
wait_container_healthy "${COMPOSE_PROJECT}-db-1"

echo "[deploy] running migrations"
MIGRATIONS_SCRIPT="${MIGRATIONS_SCRIPT:-${APP_DIR}/scripts/run_backend_migrations.sh}"
if [[ -x "${MIGRATIONS_SCRIPT}" ]]; then
    "${MIGRATIONS_SCRIPT}"
else
    echo "WARNING: migrations script not found at ${MIGRATIONS_SCRIPT}; running alembic directly"
    compose run --rm backend alembic upgrade head
fi

echo "[deploy] starting stack"
compose up -d

echo "[deploy] waiting for backend readiness"
wait_container_healthy "${COMPOSE_PROJECT}-backend-1"

echo "[deploy] waiting for frontend to serve"
for _ in $(seq 1 60); do
    if docker inspect -f '{{.State.Running}}' "${COMPOSE_PROJECT}-frontend-1" 2>/dev/null | grep -q true; then
        break
    fi
    sleep 2
done

echo "[deploy] finished: ${COMPOSE_PROJECT} is up"
compose ps
