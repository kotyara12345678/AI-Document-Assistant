#!/usr/bin/env bash
#
# Run Alembic migrations on the target stack BEFORE starting the full stack.
#
# Called by deploy.sh after the database is up and healthy. Can also be run
# manually on a server:
#   APP_DIR=/opt/ada COMPOSE_PROJECT=ada-staging COMPOSE_FILE=docker-compose.staging.yml \
#     ./run_backend_migrations.sh
#
set -euo pipefail

COMPOSE_PROJECT="${COMPOSE_PROJECT:?COMPOSE_PROJECT is required (compose project name)}"
COMPOSE_FILE="${COMPOSE_FILE:?COMPOSE_FILE is required (compose file name)}"

echo "[migrations] running alembic upgrade head (project=${COMPOSE_PROJECT})"
docker compose -p "${COMPOSE_PROJECT}" -f "${COMPOSE_FILE}" \
    run --rm --no-deps backend alembic upgrade head
echo "[migrations] done"