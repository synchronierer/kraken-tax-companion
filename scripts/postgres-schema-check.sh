#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="sprint4a_schema_${UID}_$$"
CONTAINER="${PROJECT}_postgres"
NETWORK="${PROJECT}_network"
CACHE="${XDG_CACHE_HOME:-/tmp/kraken-tax-tool-cache}"
mkdir -p "$CACHE"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker info >/dev/null
docker compose -f "$ROOT/docker-compose.yml" config -q
IMAGE="$(docker compose -f "$ROOT/docker-compose.yml" images -q backend)"
if [[ -z "$IMAGE" ]]; then
  echo "Kein Backendimage vorhanden; zuerst Backend bauen." >&2
  exit 2
fi
docker network create "$NETWORK" >/dev/null
docker run -d --name "$CONTAINER" --network "$NETWORK" \
  -e POSTGRES_USER=sprint3b -e POSTGRES_PASSWORD=synthetic-password \
  -e POSTGRES_DB=sprint3b postgres:16-alpine >/dev/null
for _ in {1..30}; do
  docker exec "$CONTAINER" pg_isready -U sprint3b >/dev/null 2>&1 && break
  sleep 1
done
docker exec "$CONTAINER" pg_isready -U sprint3b >/dev/null

docker run --rm --network "$NETWORK" --user "$(id -u):$(id -g)" \
  --entrypoint sh -e HOME=/cache/home -e PYTHONPATH=/source \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e APP_DATABASE_URL="postgresql+psycopg://sprint3b:synthetic-password@${CONTAINER}/sprint3b" \
  -v "$ROOT/backend:/source:ro" -v "$CACHE:/cache" -w /source "$IMAGE" -c '
    test -x /cache/venv/bin/python || (python -m venv /cache/venv && /cache/venv/bin/pip install ".[dev]")
    /cache/venv/bin/python - <<"PY"
from pathlib import Path
import app
path = Path(app.__file__).resolve()
print(f"app: {path}")
if not path.is_relative_to(Path("/source/app")):
    raise SystemExit("app wird nicht aus /source geladen")
PY
    /cache/venv/bin/alembic upgrade head
    /cache/venv/bin/alembic check
    /cache/venv/bin/alembic downgrade 0010_export_format_version
    /cache/venv/bin/alembic upgrade head
    /cache/venv/bin/alembic check
    /cache/venv/bin/alembic downgrade 0007_kraken_ledger_identity
    /cache/venv/bin/alembic upgrade head
    /cache/venv/bin/alembic check
  '
echo SPRINT_4A_POSTGRES_SCHEMA_OK
