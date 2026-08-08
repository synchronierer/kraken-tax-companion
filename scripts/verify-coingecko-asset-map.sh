#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${COINGECKO_BASE_URL:-${APP_COINGECKO_BASE_URL:-https://api.coingecko.com/api/v3}}"
TMP_FILE="$(mktemp /tmp/kraken-tax-coingecko-coins.XXXXXX.json)"

cleanup() {
  rm -f "$TMP_FILE"
}
trap cleanup EXIT

case "$BASE_URL" in
  https://*) ;;
  *)
    echo "FEHLER: Der CoinGecko-Endpunkt muss HTTPS verwenden." >&2
    exit 2
    ;;
esac

echo "Prüfe CoinGecko-Assetregister am konfigurierten HTTPS-Endpunkt."
curl --fail --silent --show-error \
  --max-time "${COINGECKO_VERIFY_TIMEOUT:-30}" \
  --header 'Accept: application/json' \
  "${BASE_URL%/}/coins/list" >"$TMP_FILE"

python3 - "$TMP_FILE" <<'PY'
import json
import sys
from pathlib import Path

expected = {
    "ADA": ("cardano", "ada", {"Cardano"}),
    "ATOM": ("cosmos", "atom", {"Cosmos Hub"}),
    "BTC": ("bitcoin", "btc", {"Bitcoin"}),
    "DOT": ("polkadot", "dot", {"Polkadot"}),
    "EIGEN": (
        "eigenlayer",
        "eigen",
        {"EigenCloud (prev. EigenLayer)"},
    ),
    "ETH": ("ethereum", "eth", {"Ethereum"}),
    "GRT": ("the-graph", "grt", {"The Graph"}),
    "KAVA": ("kava", "kava", {"Kava"}),
    "XTZ": ("tezos", "xtz", {"Tezos"}),
}

try:
    payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as error:
    raise SystemExit(f"FEHLER: Ungültige CoinGecko-Antwort: {error}") from error

if not isinstance(payload, list):
    raise SystemExit("FEHLER: CoinGecko /coins/list lieferte keine Liste.")

by_id: dict[str, list[dict[str, object]]] = {}
for item in payload:
    if isinstance(item, dict) and isinstance(item.get("id"), str):
        by_id.setdefault(item["id"], []).append(item)

failed = False
for asset, (provider_id, symbol, names) in expected.items():
    matches = by_id.get(provider_id, [])
    if len(matches) != 1:
        print(
            f"FEHLER: {asset} -> {provider_id}: erwartet genau einen Eintrag, "
            f"erhalten {len(matches)}.",
            file=sys.stderr,
        )
        failed = True
        continue
    item = matches[0]
    actual_symbol = item.get("symbol")
    actual_name = item.get("name")
    if actual_symbol != symbol or actual_name not in names:
        print(
            f"FEHLER: {asset} -> {provider_id}: Symbol/Name unerwartet "
            f"({actual_symbol!r}, {actual_name!r}).",
            file=sys.stderr,
        )
        failed = True
        continue
    print(f"OK: {asset} -> {provider_id} / {actual_symbol} / {actual_name}")

if failed:
    raise SystemExit(1)
print("COINGECKO_ASSET_MAP_V2_OK")
PY
