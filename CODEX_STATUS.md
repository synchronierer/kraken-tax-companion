# Codex Status

## Task

Sprint 5B.3.11C controlled production application of the three validated
user-attestation records.

## Result

`5B.3.11C_PRODUCTION_STOPPED_WRITERS_OFF`

The targeted import and projection completed, but the mandatory production
verification failed: the read-only `/api/reviews` and
`/api/financial-reviews` projections still exposed the three historical
`ledger_deposit_requires_review` rows. Writers were stopped immediately; no
repair, rollback, or second import was attempted.

## Repository State

- Approved main/origin before the run: `3a18ffeb0de19a3147ae03818d89cdf19502b561`
- No repository code or schema was changed.
- This report is on `codex-status`, never merged into `main`.

## Preflight / Evidence

- Production revision before and after: `0013_historical_transfer_evidence`
- Baseline before mutation: RawImportRecords 3103, HistoricalTransferLinks 8,
  HistoricalTransferResolutions 8, and the expected 2711/13/49/39/2759
  domain counts.
- Evidence file: `/home/lo/Backups/kraken-tax-companion/historical-sources/historical-user-attestation.csv`
- Evidence SHA256: `c3c1146828a9c3ed5401292140dbeb27dbbc9146ae03e41773404e09f8e6a218`
- Three records and confidence values matched the 5B.3.11B shadow exactly.

## Shadow Gate

The fresh shadow from the new PRE backup passed the structural gates:

- 3 attestation records accepted, 0 reused, 0 rejected
- RawImportRecords 3103 -> 3106
- HistoricalTransferLinks 8 -> 11
- HistoricalTransferResolutions 8 -> 11 (6 RESOLVED, 5 PARTIAL)
- AcquisitionLots 2711, DisposalEvents 13, FeeEvents 49,
  TradeExecutions 39, ValuationRequirements 2759 unchanged
- five historical cost-basis gaps, including the three full deposit quantities
- no acquisition, fee, disposal, zero-cost lot, or deposit-date lot

The shadow also demonstrated that the latest transformation decisions were
`ledger_historical_self_transfer_resolved`, but the API active-review gate was
not included in the earlier helper script. This was the decisive verification
gap.

## Production Mutation and Failure

The exact targeted apply ran once with network disabled:

- 3 user-attestation RawImportRecords accepted
- 3 links and 3 PARTIAL resolutions created
- final structural counts: RawImportRecords 3106, Links 11, Resolutions 11;
  central domain counts unchanged
- cost-basis gaps total 5
- no TaxCalculationRun or FinancialReviewResolution was created

The backend was briefly restarted for smoke checks, then stopped when both
review endpoints still reported deposit-review count 3 (and cost-basis gaps 5).
The underlying issue history still contains the original three rows, while the
latest decisions for all three targets are `ledger_historical_self_transfer_resolved`.
The existing `/api/reviews` implementation projects active issues by raw/code
and does not suppress them based on the latest TransformationDecision. This is
a release-blocking semantic mismatch; no code was changed.

## Backups

- PRE: `production-5b3.11/5b3.11c-pre-attestation-20261006-210517.db`
  SHA256 `e79cffcbd765cde65efff8a1b1007a8b5778fd8de10b037cc3f25610366fd6de`
- POST (before failed verification):
  `production-5b3.11/5b3.11c-post-attestation-20261006-210559.db`
  SHA256 `715d9fb423c20139c9b14b8b58bbf1e357988089431592b5cfd7809d2394db3f`
- FAILED state:
  `production-5b3.11/5b3.11c-failed-20261006-210621.db`
  SHA256 `715d9fb423c20139c9b14b8b58bbf1e357988089431592b5cfd7809d2394db3f`

All backups were created with SQLite `Connection.backup()`. Integrity checks
passed. The production backend remains stopped.

## Safety

- No rollback or improvisational repair was attempted.
- No provider/Kraken call, order, TaxCalculationRun, or review decision
  occurred.
- No code, migration, or repository file was changed.

## Changed Files

- `CODEX_STATUS.md` on `codex-status` only.
- No files changed on `main`.

## Git State

- Status report is committed and pushed only to `origin/codex-status`.
- Main remains at the approved commit and clean.

## Next Decision Required

Review the active-review projection mismatch before any further production
action. A targeted code/design correction is required to make a resolved
TransformationDecision suppress the historical deposit review without creating
a FinancialReviewResolution. Keep writers stopped and do not rerun the import.
