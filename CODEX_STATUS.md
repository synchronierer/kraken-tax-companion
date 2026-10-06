# Codex Status

## Task

Sprint 5B.3.11B offline shadow evaluation of user-attested microtask and
intermediate-wallet evidence for the three remaining historical deposits.

## Result

`5B.3.11B_USER_ATTESTATION_SHADOW_PASS`

The existing model was sufficient; no repository code or schema change was
needed. A deterministic evidence file was created outside the repository at
`/home/lo/Backups/kraken-tax-companion/historical-sources/historical-user-attestation.csv`.
No production data was changed.

## Repository State

- Main/origin before and after: `3a18ffeb0de19a3147ae03818d89cdf19502b561`
- Main working tree is clean and unchanged.
- This report is maintained on `codex-status`, never merged into `main`.

## Architecture Decision

The three attestations were imported as persistent RawImportRecords with the
new source `historical-user-attestation`, evidence level `user_attestation`,
deterministic canonical keys, and canonical fingerprints. Existing
`HistoricalTransferService` was reused without schema migration:

- transfer nature: `SELF_TRANSFER`
- basis coverage: `PARTIAL`
- resolution status: `PARTIAL`
- no source acquisition timestamp or value is asserted
- the Kraken timestamp is used only for exact target identity

The existing historical transfer projection emitted one full-quantity
`historical_cost_basis_gap` per attested deposit by using an acquisition-shaped
evidence payload with unknown occurrence time. It created no lot, valuation
requirement, fee, or disposal.

## Confidence Preservation

- DOGE economic origin: `microtask`, `user_attested`
- BTC/LTC economic origin: `microtask`, `probable`
- all three transfer nature: `own_wallet`, supported only by user attestation

The payload retains `acquisition_timestamp=unknown` and
`acquisition_value=unknown`; no attestation was upgraded to exchange or
blockchain proof.

## Shadow Results

Shadow source: fresh SQLite `Connection.backup()` copy of the post-production
backup at `/tmp/user-attestation-shadow.db`, with network disabled.

First pass:

- attestation import: 3 accepted, 0 reused
- HistoricalTransferLinks: 8 -> 11
- HistoricalTransferResolutions: 8 -> 11
- statuses: 6 RESOLVED, 5 PARTIAL
- AcquisitionLots 2711, DisposalEvents 13, FeeEvents 49
- TradeExecutions 39, ValuationRequirements 2759
- RawImportRecords 3103 -> 3106
- historical cost-basis gaps: 2 -> 5; new gaps are DOGE 318.65944000,
  LTC 0.0062935900, and BTC 0.0003000200
- no acquisition at a Kraken deposit timestamp, zero-cost lot, fee, or
  disposal was created

The previous three `ledger_deposit_requires_review` rows remain in the
append-only issue history, but each target receives the latest
`ledger_historical_self_transfer_resolved` decision. Under active/latest
decision semantics, the deposit-review subset is therefore 0. No new review
decision or FinancialReviewResolution was created.

Second identical pass:

- attestation import: 0 accepted, 3 reused
- links/resolutions remained 11/11
- domain counts remained unchanged
- cost-basis gaps remained 5
- no duplicate domain objects were created

## Tests / Quality Gates

- Shadow execution completed with the production application image and
  network isolation: PASS
- Deterministic identity/idempotency: PASS
- Confidence and unknown-date/value preservation: PASS
- No full test suite was needed because no repository code changed.

## Safety

- No production DB access or mutation beyond the prior completed deployment;
  this task used only a backup-derived shadow.
- No Historical Import, TaxCalculationRun, FinancialReviewResolution,
  provider/Kraken call, or order occurred.
- No secrets, full addresses, or full transaction IDs are included.

## Changed Files

- External offline evidence only:
  `historical-sources/historical-user-attestation.csv`
- `CODEX_STATUS.md` on `codex-status`.
- No repository files changed on `main`.

## Git State

- Status report committed and pushed only to `origin/codex-status`.
- Main remains clean at `3a18ffeb0de19a3147ae03818d89cdf19502b561`.

## Next Decision Required

Review whether user-attested transfer nature is acceptable for these three
cases. Even if accepted, the full-quantity cost-basis gaps remain and must not
be converted into zero-cost or deposit-date acquisition lots. No production
import or review resolution is authorized by this shadow result.
