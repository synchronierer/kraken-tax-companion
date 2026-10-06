# Codex Status

## Task

Controlled Sprint 5B.3.10 production historical import resumed after the
offline-source mount correction.

## Result

`HISTORICAL_PRODUCTION_IMPORT_PASS`

Migration was already at `0013_historical_transfer_evidence`; exactly one
historical import run completed after the fresh source-mount gate passed.

## Repository State

- Main deploy version: `acd428241a6dc724987533517938cdd597afaaed`
- Post-handoff documentation commit on `main`: `3a18ffeb0de19a3147ae03818d89cdf19502b561`
- `main` and `origin/main` matched after the documentation push.
- This report is maintained on `codex-status`, which must never be merged into
  `main`.

## Summary

The fresh resume preflight and source gate passed: 54 historical evidence
records (52 original plus 2 manual bookkeeping), 3,006 ledger records, and 39
TradesHistory records were available with the validated inputs. The production
run completed once, with 39/39 trade reconciliation matches and no conflicts.

## Tests / Quality Gates

- Production database integrity: PASS
- Historical import: PASS, exactly one run
- Trade reconciliation: 39 MATCHED, 0 PARTIAL, 0 PENDING, 0 CONFLICT
- Backend health after restart: healthy
- Read-only smoke checks: `/health`, `/api/reviews`,
  `/api/financial-reviews`, and frontend root returned successfully
- Active deposit-review subset: exactly 3 in both review APIs
- No provider/network call was used for the import; maintenance execution used
  network isolation

## Data / Domain Results

Final production counts:

- AcquisitionLots: 2711
- DisposalEvents: 13
- FeeEvents: 49
- RawImportRecords: 3103
- TradeExecutions: 39
- ValuationRequirements: 2759
- HistoricalTransferLinks: 8
- HistoricalTransferResolutions: 8 (6 RESOLVED, 2 PARTIAL)
- TransformationIssues: 63
- TransformationRuns: 16
- FinancialReviewResolutions: 2 (unchanged)
- TaxCalculationRuns: 2 (unchanged; no new run)

Historical valuation requirements are 4 external acquisitions and 6 external
fees. Fee provenance is 4 original-exchange network fees and 2 manual-
bookkeeping transfer fees; no buy-record fee provenance was created. The three
remaining deposit reviews are the unresolved deposit subset, and the two
historical cost-basis gaps remain auditably recorded. Transport differences
were not classified as fees or disposals.

## Safety

- No TaxCalculationRun was started.
- No FinancialReviewResolution was created or automatically decided.
- No Kraken/provider call, order, or trading operation occurred.
- Writers were stopped during database mutation and verification; the backend
  was started only after the post-backup and verification gates passed.
- All live SQLite backups used `sqlite3.Connection.backup()`.
- PRE/resume and POST backups are retained under
  `/home/lo/Backups/kraken-tax-companion/production-5b3.10/`.

## Changed Files

- `CODEX_STATUS.md` on `codex-status` only.
- No project source files were changed by the production run.

## Git State

- This status report is committed and pushed only on `origin/codex-status`.
- Main remains at the approved commit and is not modified by this status
  update.

## Next Decision Required

Next step: offline-only analysis of the three remaining unresolved deposit
reviews (DOGE 318.65944000, LTC 0.0062935900, BTC 0.0003000200). Do not
perform automatic review decisions or another Historical Import without
explicit authorization.
