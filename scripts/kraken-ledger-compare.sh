#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
CSV_FILE="${CSV_FILE:-}"
START="${START:-}"
END="${END:-}"
DIAGNOSTIC_LIMIT="${DIAGNOSTIC_LIMIT:-20}"

if [[ -z "$CSV_FILE" || ! -f "$CSV_FILE" ]]; then
  echo "CSV_FILE muss auf eine reguläre Kraken-Ledger-CSV zeigen." >&2
  exit 2
fi
if [[ -z "$START" || -z "$END" ]]; then
  echo "START und END müssen als UTC-Zeitpunkte gesetzt sein." >&2
  exit 2
fi

WORK="$(mktemp -d /tmp/kraken-ledger-compare.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT
STATUS="$(curl --silent --show-error --output "$WORK/result.json" \
  --write-out '%{http_code}' --request POST \
  --form "file=@${CSV_FILE};type=text/csv" \
  --form "start=${START}" --form "end=${END}" \
  --form "diagnostic_limit=${DIAGNOSTIC_LIMIT}" \
  "${BACKEND_URL%/}/api/kraken/ledger-compare")"
if [[ "$STATUS" != "200" ]]; then
  echo "Ledger-Abgleich fehlgeschlagen (HTTP $STATUS)." >&2
  python3 -m json.tool "$WORK/result.json" >&2 || true
  exit 1
fi
python3 -m json.tool "$WORK/result.json"

if [[ -n "${DIAGNOSTIC_OUTPUT_DIR:-}" ]]; then
  if [[ ! -d "$DIAGNOSTIC_OUTPUT_DIR" ]]; then
    echo "DIAGNOSTIC_OUTPUT_DIR muss ein vorhandenes Verzeichnis sein." >&2
    exit 2
  fi
  install -m 600 "$WORK/result.json" \
    "$DIAGNOSTIC_OUTPUT_DIR/kraken-ledger-comparison.json"
fi

if python3 - "$WORK/result.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
if result.get("ready_for_import") is not True:
    raise SystemExit(1)
print(
    "PASS: Ledger-ID-Digests stimmen überein; "
    f"{result.get('matched_ids', 0)} Einträge sind importbereit."
)
PY
then
  exit 0
fi
echo "FAIL: Der Ledger-Abgleich ist nicht importbereit." >&2
exit 1
