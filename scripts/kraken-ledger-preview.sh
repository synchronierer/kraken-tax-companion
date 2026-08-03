#!/usr/bin/env bash
set -euo pipefail

BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8000}"
START="${START:-${1:-}}"
END="${END:-${2:-}}"
DIAGNOSTIC_LIMIT="${DIAGNOSTIC_LIMIT:-0}"
OUTPUT_DIR="${OUTPUT_DIR:-}"

payload_file="$(mktemp /tmp/kraken-ledger-preview.XXXXXX.json)"
response_file="$(mktemp /tmp/kraken-ledger-response.XXXXXX.json)"
cleanup() { rm -f "$payload_file" "$response_file"; }
trap cleanup EXIT

python3 - "$START" "$END" "$DIAGNOSTIC_LIMIT" >"$payload_file" <<'PY'
import json
import sys

start, end, limit = sys.argv[1:]
payload = {"diagnostic_limit": int(limit)}
if start:
    payload["start"] = start
if end:
    payload["end"] = end
print(json.dumps(payload))
PY

status="$(curl --silent --show-error --output "$response_file" \
  --write-out '%{http_code}' --request POST \
  --header 'Content-Type: application/json' \
  --data-binary "@$payload_file" \
  "${BACKEND_URL%/}/api/kraken/ledger-preview")"

if [[ "$status" != "200" ]]; then
  echo "Kraken-Ledger-Vorschau fehlgeschlagen (HTTP $status)." >&2
  python3 -m json.tool "$response_file" >&2 || true
  exit 1
fi

python3 -m json.tool "$response_file"
if [[ -n "$OUTPUT_DIR" ]]; then
  if [[ ! -d "$OUTPUT_DIR" ]]; then
    echo "Das explizite Diagnoseverzeichnis existiert nicht: $OUTPUT_DIR" >&2
    exit 2
  fi
  resolved="$(realpath "$OUTPUT_DIR")"
  if [[ -e "$resolved/kraken-ledger-preview.json" ]]; then
    echo "Die Diagnosedatei existiert bereits und wird nicht überschrieben." >&2
    exit 2
  fi
  cp "$response_file" "$resolved/kraken-ledger-preview.json"
  chmod 600 "$resolved/kraken-ledger-preview.json"
  echo "Diagnose gespeichert: $resolved/kraken-ledger-preview.json"
fi
