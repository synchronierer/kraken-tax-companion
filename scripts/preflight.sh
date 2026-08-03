#!/usr/bin/env bash
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d /tmp/kraken-tax-preflight.XXXXXX)"
CACHE="${XDG_CACHE_HOME:-/tmp/kraken-tax-tool-cache}"
mkdir -p "$CACHE" "$WORK/frontend"
declare -A RESULT
FAILED=0

cleanup() {
  if [[ "$FAILED" -eq 0 && "${KEEP_PREFLIGHT_ARTIFACTS:-0}" != "1" ]]; then
    rm -rf "$WORK"
  else
    echo "Prüfprotokolle: $WORK"
  fi
}
trap cleanup EXIT

gate() {
  local name="$1"
  shift
  if "$@" >"$WORK/$name.log" 2>&1; then
    RESULT["$name"]="PASS"
  else
    RESULT["$name"]="FAIL"
    FAILED=1
  fi
}

backend() {
  docker run --rm --user "$(id -u):$(id -g)" --entrypoint sh \
    -e HOME=/cache/home -e PYTHONPATH=/source \
    -e PYTHONDONTWRITEBYTECODE=1 -e COVERAGE_FILE=/cache/.coverage \
    -v "$ROOT/backend:/source:ro" -v "$CACHE:/cache" -w /source \
    "$BACKEND_IMAGE" -c "$1"
}

frontend() {
  (cd "$ROOT/frontend" && "$@")
}

docker info >"$WORK/docker.log" 2>&1 || {
  echo "Docker ist für die isolierten Backendprüfungen erforderlich." >&2
  exit 2
}
BACKEND_IMAGE="$(docker compose -f "$ROOT/docker-compose.yml" images -q backend)"
if [[ -z "$BACKEND_IMAGE" ]]; then
  echo "Kein Backendimage vorhanden; zuerst: docker compose build backend" >&2
  exit 2
fi

gate backend_tools backend \
  'test -x /cache/venv/bin/python || (python -m venv /cache/venv && /cache/venv/bin/pip install ".[dev]")'
gate ruff backend '/cache/venv/bin/ruff check --no-cache .'
gate black backend '/cache/venv/bin/black --check .'
gate mypy backend '/cache/venv/bin/mypy --cache-dir /cache/mypy app'
gate backend_tests backend \
  '/cache/venv/bin/python -m pytest -o cache_dir=/cache/pytest --cov=app --cov-report=term-missing --cov-fail-under=100'
gate frontend_tests frontend npm test
gate eslint frontend npm run lint
gate typecheck frontend npm run typecheck
gate frontend_build frontend npm run build
gate markdownlint bash -c \
  "cd '$ROOT' && npx --yes markdownlint-cli2 '**/*.md' '#frontend/node_modules'"
gate git_diff_check git -C "$ROOT" diff --check

printf '\n%-22s %s\n' "Gate" "Ergebnis"
for name in backend_tools ruff black mypy backend_tests frontend_tests eslint \
  typecheck frontend_build markdownlint git_diff_check; do
  printf '%-22s %s\n' "$name" "${RESULT[$name]:-FAIL}"
  if [[ "${RESULT[$name]:-FAIL}" == "FAIL" ]]; then
    echo "  Log: $WORK/$name.log"
    tail -n 40 "$WORK/$name.log"
  fi
done
exit "$FAILED"
