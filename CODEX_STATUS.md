# Codex Status

## Task

5B.3.10A Valuation Requirement Differential Audit.

## Result

The controlled production import remains safely aborted before any production
mutation. A fresh shadow run deterministically produced 2,756 valuation
requirements instead of the expected 2,759.

## Repository State

- `main` HEAD: `75a2618659ca9183777862196e053ae3b79f8483`
- `origin/main`: `75a2618659ca9183777862196e053ae3b79f8483`
- Development checkout was clean before this status-only branch was created.
- `codex-status` is intentionally separate from `main` and must never be merged.

## Summary

The audit found exactly three missing valuation requirements for direct external
Bitcoin.de network-fee projections: ETH, BCH, and BTC. The corresponding
FeeEvents exist and are marked as requiring valuation, but the direct-fee
projection path does not create a ValuationRequirement. The three manually
specified Bittrex/Bitcoin.de fee requirements are present.

The discrepancy is deterministic and is a code defect, not nondeterminism or
contaminated source data. No code was changed for this audit.

## Tests / Quality Gates

- Existing shadow gate: production import aborted safely on the valuation-count
  mismatch.
- Fresh shadow rerun from the current PRE backup: reproduced 2,756.
- Both shadow databases passed SQLite integrity checks.
- No repository tests or quality gates were changed or bypassed.

## Data / Domain Results

- Shadow FeeEvents: 49
- Shadow ValuationRequirements: 2,756
- HistoricalTransferLinks: 8
- HistoricalTransferResolutions: 8 (6 resolved, 2 partial)
- External RawImportRecords: 52 with matching canonical keys/content hashes
- Trade reconciliation remained 39 matched, 0 partial, 0 pending, 0 conflict.

The three missing requirements correspond to direct external network fees whose
source records contain no EUR valuation. They therefore require valuation.

## Safety

- No production database was changed.
- No production migration or historical import was executed.
- No services were stopped.
- No TaxCalculationRun, review decision, provider call, or order occurred.
- No secrets or private credentials were accessed or recorded.

## Changed Files

- `CODEX_STATUS.md` on branch `codex-status` only.

## Git State

- Status report branch: `codex-status`
- This branch is separate from `main` and is for status communication only.
- Only this status file will be committed and pushed.

## Next Decision Required

Review whether the direct external fee projection should create the same
`historical_external_fee` ValuationRequirements as the explicit source-fee
projection. Do not resume production import until the decision is implemented,
tested, and the complete offline shadow gate passes.
