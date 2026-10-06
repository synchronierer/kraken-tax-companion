# Codex Status

## Task

Sprint 5B.3.11D – active-review supersession by the latest transformation
decision.

## Result

`5B.3.11D_ACTIVE_REVIEW_SUPERSESSION_PASS`

The production state was not opened or changed. The backend remains stopped.
The fix is local, uncommitted, and not deployed.

## Repository State

- Main/origin baseline: `3a18ffeb0de19a3147ae03818d89cdf19502b561`
- Main has uncommitted code, tests, and documentation changes for review.
- This report is on `codex-status`, never merged into `main`.

## Root Cause

`active_transformation_issues()` deduplicated append-only issues by
`(raw_import_record_id, code)` but ignored later `TransformationDecision`
rows. Therefore an old `ledger_deposit_requires_review` remained visible even
after the same raw record received
`ledger_historical_self_transfer_resolved` / `INTERNAL_MOVEMENT`.

## Implemented Semantics

The core helper now accepts decisions and deterministically selects the latest
decision per raw record using:

1. `TransformationRun.started_at`
2. `TransformationDecision.decided_at`
3. decision UUID string

Missing run provenance fails safe and does not suppress an issue. A latest
`REVIEW_REQUIRED` or `CONFLICT` keeps only the issue whose code equals the
current decision reason. Any valid latest non-reviewing decision suppresses
older transformation review issues. Historical rows remain append-only.

`historical_cost_basis_gap` is retained as an independent active evidence gap,
because transfer classification does not resolve missing acquisition basis.
FinancialReviewResolution remains separate and is never created by this logic.

Updated consumers:

- `/api/reviews`
- `/api/financial-reviews` and suggestion generation
- dashboard active-review counts

## Tests / Quality Gates

- Focused active-review projection tests: 7 passed
- Full backend suite: 676 passed
- Full backend coverage: 100.00% (7387 statements)
- Ruff: PASS
- Black --check: PASS
- mypy `app`: PASS
- `docker compose config -q`: PASS
- `git diff --check`: PASS
- Markdownlint was unavailable in the host environment.

Tests cover same-code and changed-code review supersession, non-reviewing
internal movement, conflict precedence, missing-run fail-safe behavior,
deterministic tie-breaking, persistent cost-basis gaps, and historical issue
retention.

## Offline Real-State Gate

The failed-state backup
`production-5b3.11/5b3.11c-failed-20261006-210621.db` was used read-only.
No transformation or import was run. Structural counts remain:

- RawImportRecords 3106
- HistoricalTransferLinks 11
- HistoricalTransferResolutions 11
- AcquisitionLots 2711, DisposalEvents 13, FeeEvents 49
- TradeExecutions 39, ValuationRequirements 2759

With the corrected helper, the existing latest decisions project:

- active `ledger_deposit_requires_review`: 0
- active `historical_cost_basis_gap`: 5
- `/api/reviews` deposit subset: 0
- `/api/financial-reviews` deposit subset: 0

No FinancialReviewResolution or TaxCalculationRun changed. Production remains
writer-stopped and was not used for the gate.

## Safety

- No production DB mutation, migration, import, rollback, or repair.
- No backend start, provider/Kraken call, order, TaxCalculationRun, or review
  decision.
- No code was committed, pushed, or deployed.

## Changed Files

- `backend/app/core/transformation.py`
- `backend/app/api/financial_reviews.py`
- `backend/app/api/workflows.py`
- `backend/app/database/dashboard_queries.py`
- `backend/tests/test_active_review_projection.py`
- `docs/DEVELOPMENT_STATE.md`

## Git State

The above files are uncommitted local changes on `main`; no commit or push was
performed. `git diff --check` is clean. `git diff --stat` reports 6 changed
files, 204 insertions, and 45 deletions.

## Next Decision Required

Review the supersession implementation and the failed-state offline gate.
Authorize a separate controlled deployment only after review. Keep production
writers stopped; do not rerun the attestation apply or perform any review
resolution automatically.
