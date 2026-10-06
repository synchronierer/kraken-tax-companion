# Codex Status

## Task

Sprint 5B.3.11A forensic, offline-only audit of the three remaining historical
Kraken deposit reviews.

## Result

No production or repository data was changed. No review decision was made.
All three cases remain unresolved and fail closed.

## Repository State

- `main`/`origin/main` before the audit: `3a18ffeb0de19a3147ae03818d89cdf19502b561`
- Main working tree remains clean and was not modified.
- This report is on `codex-status`, which must never be merged into `main`.

## Summary

The production database was inspected read-only at revision
`0013_historical_transfer_evidence`. Exactly three active
`ledger_deposit_requires_review` records were identified:

| Case | Asset / amount | UTC timestamp | Evidence classification |
|---|---|---|---|
| 1 | DOGE `318.65944000` | 2021-01-13 01:07:27 | `ECONOMIC_ORIGIN_UNKNOWN` |
| 2 | LTC `0.0062935900` | 2021-02-01 15:21:32 | `ECONOMIC_ORIGIN_UNKNOWN` |
| 3 | BTC `0.0003000200` | 2022-07-04 11:03:07 | `NOT_ENOUGH_EVIDENCE` |

The Kraken ledger identity was preserved internally; this report omits full
transaction IDs and addresses. None of the three records has a
HistoricalTransferLink or HistoricalTransferResolution.

## Tests / Quality Gates

- Read-only production revision and active-review count: PASS
- Local source inventory and exact-text searches: PASS
- No web search, exchange API, provider call, TaxCalculationRun, order, or
  FinancialReviewResolution was performed.
- No code changes were necessary; no software defect was found.

## Data / Domain Results

### DOGE 318.65944000

The Kraken deposit is the only ledger event at that timestamp. The same-asset
Kraken history contains the previously resolved 2020 self-transfer of
`11402.55352573` DOGE, then this deposit, followed later by trades; no matching
withdrawal, refid, or source transaction was found for the open amount.

The local Bittrex OrderHistory proves two known acquisitions of
`841.39125000` and `10444.96815628` DOGE. Their sum is `11286.35940628` against
the documented Bittrex balance `11404.55352573`; the residual is exactly
`118.19411945` DOGE, already represented as a partial-basis gap for the
resolved 2020 transfer. That balance arithmetic does not identify the 2021
deposit's economic origin, and no source-side withdrawal or dated record for
`318.65944000` exists. The 2021 Kraken trades show later spending, not origin.

Consequence: transfer nature and acquisition basis are not provable for this
deposit. No lot, fee, disposal, or resolution may be created.

### LTC 0.0062935900

The Kraken ledger contains the resolved 2020 self-transfer of `1.4826627600`
LTC immediately before this separate deposit. No matching external source
withdrawal, refid, or transaction record for `0.0062935900` LTC exists in the
local sources.

The Bittrex evidence proves a known `1.48860041` LTC acquisition and a
separate documented balance of `1.49266276` LTC. The difference is
`0.00406235` LTC, the existing partial-basis gap; it is not evidence for the
2021 deposit. No amount-only inference is accepted.

Consequence: economic origin remains unknown. No lot, fee, disposal, or
resolution may be created.

### BTC 0.0003000200

The local Bitcoin.de statement contains the fully evidenced 2021 `0.50000000`
BTC wallet migration and its `0.00007986` BTC network fee, plus the documented
`0.00000980` BTC external residual. It contains no 2022-07-04 withdrawal or
other record that can be linked exactly to this deposit.

Kraken history shows this small deposit between unrelated BTC trading activity
and later activity. The later 2025 three-part wallet return belongs to a
separate, user-attested chain and cannot be back-attributed to this 2022
deposit. No exact source quantity, timestamp, stable ID, or transfer chain was
found.

Consequence: evidence is insufficient even to establish a source transfer;
the known 2021/2025 balances are not provenance for this amount. No lot, fee,
disposal, or resolution may be created.

## Evidence Classification and Next Questions

No case reaches `SOURCE_PROVEN`, `SOURCE_PARTIAL`, or
`OWNERSHIP_ATTESTATION_NEEDED` on the available local evidence. Optional,
non-leading questions for a later review are:

- DOGE: Do you remember whether this 2021 DOGE deposit came from an external
  account or wallet, and is there a contemporaneous withdrawal/export record?
- LTC: Do you remember the source account or wallet for this small LTC
  deposit, and is there a dated withdrawal record?
- BTC: Do you remember whether this 2022 BTC deposit came from an external
  wallet or service, and can you identify a contemporaneous withdrawal or
  source statement?

User memory alone would establish at most an attested transfer nature; it does
not establish acquisition date or cost basis. Any future resolution must remain
fail closed and preserve `historical_cost_basis_gap` where basis is incomplete.

## Safety

- Production DB was read-only inspected; no migration, import, mutation,
  review decision, or rollback occurred.
- No code, schema, or configuration changed.
- No secrets, `.env` values, full private addresses, or full transaction IDs
  are included.
- Zero-cost lots and deposit-date acquisition lots remain explicitly
  prohibited.

## Changed Files

- `CODEX_STATUS.md` on `codex-status` only.
- No files changed on `main`.

## Git State

- Report committed and pushed only to `origin/codex-status`.
- Main remains clean at its existing commit.

## Next Decision Required

Review the three fail-closed classifications. Do not create resolutions or
restart Historical Import unless new, exact, auditable external evidence is
provided.
