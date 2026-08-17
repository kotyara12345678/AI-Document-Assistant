#!/bin/sh
# Startup: apply any pending Alembic migrations, then either run the command
# passed as arguments (e.g. tests) or launch uvicorn. Retries migrations until
# the database is reachable so a slow-starting Postgres does not abort startup.
set -e

echo "Running database migrations..."
n=0
until alembic upgrade head; do
    n=$((n + 1))
    if [ "$n" -ge 30 ]; then
        echo "Migrations failed after $n attempts; continuing." >&2
        break
    fi
    echo "Migration attempt $n failed, retrying in 2s..."
    sleep 2
done

if [ "$#" -gt 0 ]; then
    echo "Delegating to provided command: $*"
    exec "$@"
fi

echo "Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
