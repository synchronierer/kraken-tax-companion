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
- `bittrex-manual-transfer-evidence.csv`: explicitly labeled manual bookkeeping
  evidence for the two historical Bittrex transfer fees.

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
DEPLOYED.

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

Deployment validation:

- Backend health after deployment: `HEALTH=healthy`;
- code deployed from commit
  `bcde2908ca1235cf73cf016713eb2a3d45a2e993`;
- PRE backup: `pre-5b3-7-deploy-20261004-195922.db`;
- POST backup: `post-5b3-7-deploy-20261004-200123.db`;
- PRE logical SHA256:
  `c97faf16c110c86bb1d33f59513e6cc50d2325f381e99999107f0a73500ff869`;
- POST logical SHA256:
  `c97faf16c110c86bb1d33f59513e6cc50d2325f381e99999107f0a73500ff869`;
- tables: 36;
- `LOGICAL_DB_UNCHANGED=true`;
- no schema change and no new Alembic migration in 5B.3.7;
- no Historical Import was performed in production.

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

## Sprint 5B.3.8 Phase B.1 – Historical transfer evidence

Status: COMMITTED IN THE CURRENT DEVELOPMENT HEAD, NOT DEPLOYED, NOT USED FOR
PRODUCTION DATA.

B.1 adds the smallest persistent, auditable evidence model:

- `HistoricalTransferLink` records versioned source records, target identity,
  canonical asset, exact quantities, transfer nature, basis coverage, direct
  fees, transport differences, and evidence/resolution hashes;
- `HistoricalTransferResolution` records the auditable resolution status and
  evidence level;
- migration `0013_historical_transfer_evidence` creates both tables and has not
  been run against production.

The offline source adapters import Bitcoin.de Account Statement and Bittrex
OrderHistory as deterministic `RawImportRecord` batches. Unknown source types,
missing dates, missing costs, invalid decimals, and ambiguous assets fail closed.
Complete evidence can project an original-date `AcquisitionLot` and directly
evidenced `FeeEvent` with provenance to the external raw record. Incomplete
basis produces `historical_cost_basis_gap`; no zero-cost or Kraken-deposit-date
lot is created. Transfer nature and cost-basis coverage remain separate
dimensions, and stable keys make repeated imports/resolutions idempotent.

Validation completed:

- focused B.1 tests: 12 passed;
- full backend suite: 657 passed;
- backend coverage: 100%;
- Ruff: PASS;
- Black: PASS;
- Mypy results are environment-specific. In the earlier local preflight
  virtualenv, canonical command `mypy backend/app` reported 12 baseline
  errors at B.1 commit `67bee35132ebaf361616f2c1291934877afc9fc1`, exclusively
  in `app/api/tax.py` and `app/api/workflows.py`; the recorded output was
  byte-identical for the B.2 working tree. In the reproducible project test
  container, Python 3.12.10 with Mypy 1.20.2, the same command reports
  `Success: no issues found` for both the B.1 reference and the current tree.
  The repository configuration is unchanged (`python_version = "3.12"`,
  strict mode, Pydantic plugin). The earlier virtualenv package/toolchain
  state is not reproducible from the current container, so the 12-error result
  remains documented as local baseline technical debt rather than a universal
  project result. 5B.3.9B changes neither `app/api/tax.py` nor the underlying
  tax typing and introduces no new Mypy errors in either compared run.
- no provider access, production database access, TaxCalculationRun,
  review decision, order, or Historical Import was performed.

## Sprint 5B.3.8 Phase B.2 – External historical deposit evidence

Status: IMPLEMENTED, OFFLINE-REAL-DATA VALIDATED, COMMITTED IN THIS CHANGE,
NOT DEPLOYED, NOT USED FOR PRODUCTION DATA.

B.2 adds deterministic loaders for the supplied Bitcoin.de and Bittrex exports,
projects only explicitly linked historical source records, preserves original
acquisition dates and costs, records valuation requirements where an access
date is known but EUR value is absent, and keeps partial cost-basis gaps as
`historical_cost_basis_gap`. Kraken deposit transformation resolves only when
the link fingerprint, deposit type, canonical asset, and exact amount match;
otherwise the existing review path remains active.

The offline real-data gate used a fresh `sqlite3.Connection.backup()` copy of
the pristine sandbox. It imported the complete local ledger, complete local
trade history, and the five supplied historical source files with no network.
Eight of the eleven historical deposit cases resolved as explicit historical
self-transfers; three remain `ledger_deposit_requires_review`. No Kraken-date
acquisition is created for a resolved deposit. A second identical
transformation pass created no additional domain objects.

Validated gate totals after the first pass were AcquisitionLots 2711,
DisposalEvents 13, FeeEvents 49, TradeExecutions 39, and
ValuationRequirements 2759. Ledger/trade reconciliation was 39/39 matched;
the ledger pass reported three remaining deposit reviews and zero conflicts.

Focused B.2 tests: 18 passed. The full backend suite ran 663 tests successfully
in the offline container with 100% coverage. Ruff and Black pass. Mypy has the
environment-specific baseline described above; B.2 introduces no new error in
either the earlier local baseline output or the current test-container run.

No provider access, production database access, TaxCalculationRun,
automatic review decision, order, or production Historical Import was
performed.

## Sprint 5B.3.9 – Active review projection

The initial 5B.3.9 pre-production dry-run correctly kept domain projections
idempotent, but exposed an active-review projection defect: three unresolved
deposit facts were persisted once per transformation run and therefore
appeared as six active review rows. `TransformationIssue` and
`TransformationDecision` remain intentionally run-scoped append-only history;
their historical rows are not deduplicated or deleted.

5B.3.9B adds a shared deterministic active-review projection keyed by
`raw_import_record_id + issue.code`. It selects the newest transformation run,
then `occurred_at`, then the stable issue ID. The projection is used by the
review API, financial-review API, dashboard counts, and suggestion generation.
Historical detail and run history remain available. Confirmed financial
review resolutions continue to apply by RawImportRecord identity.

Regression coverage verifies two runs/two issues produce one active case,
three records across two runs produce three active cases, deterministic
representative selection, API/dashboard projection, suggestion behavior,
resolution of all historical representatives, and separation of issue codes.

A fresh pre-production dry-run was performed from a SQLite-backup copy of the
current production baseline. Migration `0012 -> 0013` succeeded. The first
pass produced AcquisitionLots 2711, DisposalEvents 13, FeeEvents 49,
TradeExecutions 39, and ValuationRequirements 2759. The second identical pass
left all domain counts unchanged. Six persisted
`ledger_deposit_requires_review` history rows (three facts in two runs) project
to three active deposit reviews; the two historical cost-basis gaps remain two
active gaps. Historical transfer links/resolutions remain 8/8 (6 resolved,
2 partial). No production database was mutated.

Active review cases are fachlich idempotent; historical run issues remain
append-only. No production migration, Historical Import, TaxCalculationRun,
automatic review decision, provider access, or order was performed.

## Sprint 5B.3.10B – Direct historical fee valuation

The 5B.3.10 production-import attempt was safely stopped before any
production mutation. Its shadow validation found 2756 instead of the expected
2759 valuation requirements. The cause was that direct historical fees created
their `FeeEvent` without the corresponding `ValuationRequirement`, while the
`source_fee_quantity` path did create one.

5B.3.10B centralizes historical fee projection in `_project_fee()`. Every
historical FeeEvent requiring valuation now receives exactly one
`historical_external_fee` ValuationRequirement, regardless of whether it came
from a source fee quantity or a direct fee record. Existing FeeEvent and
ValuationRequirement identities are reused, so repeated projection is
idempotent. No EUR value is invented and no FeeEvent is converted into a
DisposalEvent.

The focused direct-fee tests cover missing EUR valuation, repeated identical
fees, distinct equal BCH fees, and the existing source-fee path. The full
backend suite passed with 668 tests and 100% coverage; Ruff, Black, Mypy,
Markdownlint, Docker Compose validation, and `git diff --check` passed.

A fresh SQLite-backup shadow copy was used at
`/home/lo/Backups/kraken-tax-companion/production-5b3.10/5b3.10b-dry-run-work.db`.
The complete offline process produced AcquisitionLots 2711, DisposalEvents
13, FeeEvents 49, RawImportRecords 3101, TradeExecutions 39, and
ValuationRequirements 2759. External projection contributed 18 AcquisitionLots,
6 FeeEvents, 4 historical acquisition requirements, and 6 historical fee
requirements. All six historical fee events have exactly one linked valuation
requirement. The second identical run left all domain counts unchanged.

Historical transfer resolutions remain 8/8 (6 resolved, 2 partial), with eight
resolved self-transfer decisions and three unresolved deposit reviews. The two
cost-basis gaps remain DOGE 118.19411945 and LTC 0.00406235. Transport
differentials remain informational and are not projected as fees or disposals.
Production remains unchanged; no production migration or Historical Import was
performed.

The subsequent provenance audit failed 5B.3.10B: the six FeeEvents had the
correct counts and valuation links, but their `DomainProvenance` pointed to
acquisition/buy RawImportRecords (and source-fee projection timestamps) rather
than to the fee-bearing source records. Production remains blocked.

## Sprint 5B.3.10C – Source-true historical fee provenance

5B.3.10C corrects the provenance without a schema migration. Historical fee
projection now receives an explicit fee source RawImportRecord instead of
implicitly selecting the first acquisition source record. Bitcoin.de
`network_fee` records therefore provide the FeeEvent identity, timestamp, and
provenance. The four direct Bitcoin.de fees remain distinct, including the two
equal BCH amounts.

The two Bittrex withdrawal fees are sourced from the separate
`historical-manual-bookkeeping` import of the supplied manual evidence CSV.
The Bittrex OrderHistory remains the acquisition source. Manual evidence stores
the source document hash, `manual_bookkeeping` evidence level, and
`occurred_at_precision=date`; the technical UTC midnight normalization is not
treated as an exact historical time.

The fresh offline shadow gate used a new SQLite-backup-derived copy of the
pristine 5B.3.10 baseline with networking disabled. External source import
accepted 54 records (52 original exchange records plus 2 manual bookkeeping
records), with no rejections. Final counts were AcquisitionLots 2711,
DisposalEvents 13, FeeEvents 49, RawImportRecords 3103, TradeExecutions 39,
and ValuationRequirements 2759. Historical transfer links/resolutions remain
8/8 (6 resolved, 2 partial), with three active deposit reviews and two
historical cost-basis gaps.

The provenance gate passed: four FeeEvents point to Bitcoin.de `network_fee`
records with original-source evidence, and two point to manual-bookkeeping
records. Every FeeEvent has exactly one `historical_external_fee`
ValuationRequirement. A second identical import/projection reused all 54
external records, 3006 ledger records, and 39 trades; all domain counts and
fee requirements remained unchanged. No production database, migration,
Historical Import, TaxCalculationRun, review decision, provider, or order was
used.

## Current next step

### Sprint 5B.3.11C / 5B.3.11D – User-attestation apply and review projection

5B.3.11C applied the three previously validated user-attestation records once.
The structural data apply succeeded (RawImportRecords 3106, HistoricalTransfer
Links 11, HistoricalTransferResolutions 11 with 6 resolved and 5 partial;
central domain counts unchanged). The production smoke gate then exposed an
active-review projection bug, so the backend remains stopped and no rollback,
reimport, or repair was performed.

5B.3.11D implements the general latest-TransformationDecision supersession
rule in the core active-review projection and all consumers. A latest
review/conflict decision keeps only its matching current issue; a latest
non-reviewing decision suppresses historical review issues for that raw record.
Missing run provenance fails safe. `historical_cost_basis_gap` remains an
independent active audit gap until its evidence is supplied.

The offline gate against the failed-state backup passed: active deposit reviews
project to 0, active cost-basis gaps remain 5, and the structural counts remain
unchanged. Production itself was not changed by 5B.3.11D, and the backend must
remain stopped until this change is reviewed and separately deployed.

Next step: review the code/test diff and authorize a controlled deployment of
the projection fix only. Do not rerun the attestation import, start a
TaxCalculationRun, or make a FinancialReviewResolution automatically.

## Handoff discipline

Whenever this file is updated:

- describe current facts, not plans presented as completed work;
- distinguish between implemented, tested, offline-real-data validated,
  committed, pushed, deployed, and production-data-applied;
- include the exact next action;
- remove stale next-action text;
- never include credentials or raw private transaction records.
