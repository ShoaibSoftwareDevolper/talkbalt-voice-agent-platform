#!/usr/bin/env bash
# Runs the platform API for development: its Postgres and Redis containers, migrations, and the API
# on port 8001 with auto-reload. Ctrl+C stops the API; the containers keep running.
#
# First time only: bash scripts/setup_docker_db.sh && bash scripts/setup_docker_redis.sh talkbalt-platform-redis 6382
set -euo pipefail
cd "$(dirname "$0")/.."

docker start talkbalt-postgres talkbalt-platform-redis >/dev/null
uv run alembic upgrade head
exec uv run uvicorn api.main:app --port 8001 --reload
