#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT="sprint3b_validation_${UID}_$$"
WORK="$(mktemp -d /tmp/sprint3b-validation.XXXXXX)"
export BUILDX_CONFIG="$WORK/buildx"
mkdir -p "$BUILDX_CONFIG"

compose() {
  docker compose -p "$PROJECT" -f "$ROOT/docker-compose.yml" "$@"
}
cleanup() {
  compose down --volumes --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

"$ROOT/scripts/preflight.sh"
"$ROOT/scripts/postgres-schema-check.sh"
docker info >/dev/null
compose config -q
compose build
export BACKEND_PORT=18080
export FRONTEND_PORT=15173
compose up -d
for _ in {1..60}; do
  curl -fsS http://127.0.0.1:15173/api/dashboard >"$WORK/dashboard.json" \
    2>/dev/null && break
  sleep 1
done
curl -fsS http://127.0.0.1:18080/health >/dev/null
curl -fsS http://127.0.0.1:15173/ >/dev/null
curl -fsS http://127.0.0.1:15173/api/dashboard >/dev/null
curl -fsS http://127.0.0.1:18080/openapi.json >"$WORK/openapi.json"
grep -q '"/api/kraken/ledger-compare"' "$WORK/openapi.json"
grep -q '"/api/kraken/ledger-import"' "$WORK/openapi.json"

cat >"$WORK/trades.csv" <<'CSV'
txid,ordertxid,pair,time,type,ordertype,price,cost,fee,vol
BUY,B1,XXBTZEUR,2026-01-02 03:04:05,buy,limit,100,200,2,2
SELL,S1,XXBTZEUR,2026-02-02 03:04:05,sell,limit,150,150,1,1
CSV
curl -fsS -F "file=@$WORK/trades.csv;type=text/csv" \
  'http://127.0.0.1:15173/api/imports/kraken?transform=true' >"$WORK/import.json"
curl -fsS -X POST http://127.0.0.1:15173/api/valuations >"$WORK/valuation.json"
curl -fsS -X POST -H 'Content-Type: application/json' -d '{"year":2026}' \
  http://127.0.0.1:15173/api/tax-calculations >"$WORK/tax-run.json"
RUN_ID="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$WORK/tax-run.json")"
curl -fsS http://127.0.0.1:15173/api/tax-journal?year=2026 >"$WORK/journal.json"
curl -fsS http://127.0.0.1:15173/api/lot-allocations?year=2026 >"$WORK/fifo.json"
for kind in tax_journal_csv tax_report_pdf; do
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"tax_calculation_run_id\":\"$RUN_ID\",\"kind\":\"$kind\"}" \
    http://127.0.0.1:15173/api/exports >"$WORK/export-$kind.json"
  URL="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["download_url"])' "$WORK/export-$kind.json")"
  curl -fsS "http://127.0.0.1:15173$URL" -o "$WORK/$kind"
done
compose logs >"$WORK/compose.log"
if grep -E 'Traceback|ArgumentError|API[_-]?KEY|synthetic-password' "$WORK/compose.log"; then
  echo "Unerwarteter Fehler oder Secret in den Logs." >&2
  exit 1
fi
grep -R -E 'localhost:8000|127\.0\.0\.1:8000|backend:8000' \
  "$ROOT/frontend/dist" && exit 1 || true
grep -R -E 'KRAKEN_API_KEY|KRAKEN_API_SECRET|synthetic-secret' \
  "$ROOT/frontend/dist" && exit 1 || true
echo SPRINT_3B_HOST_VALIDATION_OK
