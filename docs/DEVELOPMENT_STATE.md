# Development State

> This file is the current handoff state for developers and coding agents.
> Read it before starting work.
> Update it whenever a meaningful milestone changes the state described here.
> Do not store secrets or private financial transaction data here.

## Repository

- Repository: `synchronierer/kraken-tax-companion`
- Main branch: `main`
- At every session start, determine the live Git state with
  `git status --short`, `git rev-parse HEAD`, and `git log -5 --oneline`.
- When needed, run `git fetch` and compare the local branch with
  `origin/main`.
- This file records stable development milestones, not permanently asserted
  volatile Git state.

## Product / architecture snapshot

The application currently supports:

- immutable Kraken Ledger historical import;
- read-only Kraken TradesHistory import;
- strict trade/ledger reconciliation;
- AcquisitionLots, DisposalEvents, FeeEvents, and TradeExecutions;
- historical rewards and staking;
- fiat funding classification;
- strict internal autoallocation classification;
- strict Futures/Spot internal classification;
- FIFO, tax, valuation, and reporting;
- review workflow;
- sale planner and dry-run simulation;
- read-only live balance and reconciliation.

## Production state

- No full historical backfill has been applied to production.
- Production therefore does not yet represent the complete historical cost basis.
- Live sale/trading simulation remains blocked until historical holdings and
  cost basis reconcile.
- Existing TaxCalculationRuns must not be replaced or supplemented automatically.
- No real order is authorized.
- Production historical-import mutation requires explicit approval.

## Historical offline source set

The offline source set consists of the following purposes only:

- `kraken-ledger-history-full.json`: complete Ledger snapshot;
- `kraken-trade-history-full.json`: complete TradesHistory snapshot;
- pristine historical sandbox database bases;
- a sandbox working directory for SQLite-backup-derived copies.

The file previously named
`/home/lo/Backups/kraken-tax-companion/historical-sandbox-base.db` is no longer
pristine because earlier sandbox runs changed it.

The following databases were verified to contain the original pristine base counts:

- `/home/lo/Backups/kraken-tax-companion/pre-5b3.db`;
- `/home/lo/Backups/kraken-tax-companion/historical-sandbox-run.db`.

Verified pristine counts:

- AcquisitionLots: 493
- DisposalEvents: 0
- FeeEvents: 0
- RawImportRecords: 500
- TradeExecutions: 0
- TransformationIssues: 58
- TransformationRuns: 6
- ValuationRequirements: 493

Neither pristine source may be overwritten. Future sandbox runs must create a
new working copy using SQLite backup semantics.

## Historical transformation milestones

### 5B.3.4 – Autoallocation

- Strict pair-based classification is implemented.
- 70 ledger rows / 35 pairs are classified as internal.
- No broad subtype shortcut is used.
- Reason code: `ledger_autoallocation_internal_pair`.

### 5B.3.5 – Fiat funding

- Kraken fiat deposits and withdrawals are classified as funding movements.
- No crypto acquisition or disposal projections are created.
- Reason code: `ledger_fiat_funding_movement`.

### 5B.3.6 – Futures / Spot

- Strict Kraken Futures-to-Spot and Spot-to-Futures transfer classification is
  implemented.
- Reason code: `ledger_futures_spot_internal_transfer`.
- This sprint is committed and deployed at `f1d654fc55782d61ffb7e2af9c6b0ed9931a0463`.

### 5B.3.7 – ETH2 → ETH Shapella migration

Status: IMPLEMENTED, TESTED, REAL-DATA OFFLINE VALIDATED, COMMITTED, PUSHED,
NOT DEPLOYED.

Code commit: `bcde2908ca1235cf73cf016713eb2a3d45a2e993`.

Implementation intent:

- strict historical pair recognition;
- raw assets exactly `ETH2` and `XETH`;
- canonical asset `ETH`;
- exact Decimal amount pairing;
- ETH2 negative and XETH positive;
- zero fees;
- separation of at most 24 hours;
- explicit UTC window `2023-04-17 <= timestamp < 2023-04-21`;
- exactly two candidates per amount group;
- ambiguity fails closed;
- pairs may span target and context import sessions;
- both legs become `INTERNAL_MOVEMENT`;
- reason code: `ledger_eth2_to_eth_shapella_migration`;
- valid migration legs create no AcquisitionLot, DisposalEvent, FeeEvent,
  TradeExecution, ValuationRequirement, or Reward.

Representation rule:

- API-normalized records require `api_type=staking`, empty `api_subtype`,
  `type=earn`, and `subtype=reward`;
- CSV records without API metadata require `type=staking` and an empty `subtype`;
- incomplete or inconsistent API metadata must fail closed and must not fall
  through as CSV.

Current automated validation:

- relevant transformation tests: 207 passed;
- full backend suite: PASS;
- coverage: 100%;
- Ruff, Black, and mypy: PASS;
- frontend tests, lint, typecheck, and build: PASS;
- Markdownlint: PASS;
- Docker Compose config: PASS;
- `git diff --check`: PASS.

The real offline historical sandbox gate passed from the verified pristine
`pre-5b3.db` base.

Import and reconciliation results:

- Ledger accepted: 2510; reused: 496; rejected: 0;
- Trades accepted: 39;
- TradesHistory reconciliation: 39 MATCHED, 0 PARTIAL, 0 PENDING, 0 CONFLICT.

First Ledger transformation:

- checked_records: 2510
- acquisitions: 2161
- disposals: 4
- fee_events: 4
- internal_movements: 255
- review_cases: 11
- conflicts: 0
- rewards: 2158
- valuation_requirements: 2169

Expected key decisions:

- `ledger_autoallocation_internal_pair`: 70
- `ledger_fiat_funding_movement`: 37
- `ledger_futures_spot_internal_transfer`: 2
- `ledger_eth2_to_eth_shapella_migration`: 4
- `ledger_trade_reconciled_to_trade_history`: 78
- `ledger_deposit_requires_review`: 11
- `reward_amount_not_positive`: 0

Remaining Ledger problems consist exclusively of
`ledger_deposit_requires_review`.

Trade transformation:

- acquisitions: 39
- disposals: 9
- fee_events: 39
- trade_executions: 39
- valuation_requirements: 87
- review_cases: 0
- conflicts: 0

Final domain totals after Ledger and Trade transformations:

- AcquisitionLots: 2693
- DisposalEvents: 13
- FeeEvents: 43
- TradeExecutions: 39
- ValuationRequirements: 2749

The second Ledger and Trade transformation passes created no new
AcquisitionLots, DisposalEvents, FeeEvents, TradeExecutions, or
ValuationRequirements. Ledger reused 2169 objects and Trade reused 39 objects.
`domain_counts_unchanged=true` and `unexpected_domain_duplicates=false`.

Terminal gate:

`HISTORICAL_SANDBOX_COMBINED_PASS`

The sandbox terminal result was `HISTORICAL_SANDBOX_COMBINED_PASS`. The listed
counts are validation evidence, not artificial PASS conditions in the probe.

## Current sandbox tooling

A temporary offline probe exists at `/tmp/historical_sandbox_probe.py` and is
not part of the repository.

It accepts `--db`, `--ledger`, and `--trades`; creates its own working SQLite
copy; uses the existing application import, reconciliation, and transformation
logic; checks 39/39 TradesHistory reconciliation; performs first and second
transformations; reports decision reason counts; and checks idempotency.

Run sandbox validation with networking disabled. Do not depend on `/tmp`
artifacts across days or reboots. If the probe becomes operationally important
long-term, create a sanitized repository-maintained diagnostic tool in a future
dedicated change rather than silently depending on a temporary file.

## Current next step

1. Deploy the committed 5B.3.7 code.
2. During deployment, verify PRE/POST logical SQLite integrity.
3. Do not perform a Historical Import in production.
4. Update this file with the deployment status after deployment.
5. Reconcile the remaining 11 crypto-deposit reviews individually against
   historical external source evidence.

## Handoff discipline

Whenever this file is updated:

- describe current facts, not plans presented as completed work;
- distinguish between implemented, tested, offline-real-data validated,
  committed, pushed, deployed, and production-data-applied;
- include the exact next action;
- remove stale next-action text;
- never include credentials or raw private transaction records.
