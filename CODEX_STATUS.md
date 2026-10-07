# Codex Status

## Sprint 5B.3.12A – Forensic inventory of historical cost-basis gaps

Analysis was read-only against the SQLite `Connection.backup()` copy
`/home/lo/Backups/kraken-tax-companion/production-5b3.11/5b.3.11e-post-deploy-121428.db`.
No live database, import, transformation, TaxCalculationRun, review decision,
provider, or order was used. Main remained unchanged at
`2a48f68d033d4a5ffbd4d51b535d1e129b242ae7`.

### Source inventory

| Source | Hash / period / scope | Forensic value |
|---|---|---|
| `kraken-ledger-history-full.json` | SHA-256 `dd78daf2ddae5350b25120edfdce7f5d8f545f8f473bf03deb6b8494a016c97f`; 3006 unique records; 2020-12-22 through 2026-09-26 | Primary Kraken ledger targets and surrounding events |
| `kraken-trade-history-full.json` | SHA-256 `53b47bae0389f0643b4e2ace21d1e9f828da165b67c3039e768d7a20d0e226d7`; 39 records; 2021-01-05 through 2025-08-08 | Kraken trade context; no source record for any gap |
| Bitcoin.de BTC statement | SHA-256 `d17428e83f54c477777c621797c8017c2559bd2871dfd5d45fa9fc5d5d3854af`; 2017-10-18 through 2023-02-09 | Primary BTC purchases, withdrawals, and network fees |
| Bitcoin.de BCH statement | SHA-256 `186842aeac9ea05a874476ce215c625346846d5448a4ad2bf5ca9bc22b7a3c36`; 2017-10-18 through 2022-02-08 | Primary BCH purchases and withdrawals |
| Bitcoin.de ETH statement | SHA-256 `ba818729ad02db208531986a87c0cb3b15498750038e230059a4be1dfe3a87e7`; 2017-10-18 through 2020-12-22 | Primary ETH purchases and withdrawal fee |
| `BittrexOrderHistory_2018.csv` | SHA-256 `d540cc1bcfd4f6fa2ecb963f0c8eec784b4a74376ae4b3ef5518d0e006f2339e`; 2018-01-17 through 2018-02-25 | Primary Bittrex buy/sell history |
| `BittrexOrderHistory_2020.csv` | SHA-256 `6b17456b50d4b24c53a37279f177ba5001e6f4d8298ff6086cb059bcb88914de`; 2020-07-08 | Primary DOGE buy history; no withdrawal/fee record |
| `bittrex-manual-transfer-evidence.csv` | SHA-256 `2549428e9e61733ac64d45cb9d6feef98829b3f264a9be7d51937a3bfa16d748`; date-only 2020-12-22 | Manual bookkeeping evidence for the two 2020 transfer fees |
| `historical-user-attestation.csv` | SHA-256 `c3c1146828a9c3ed5401292140dbeb27dbbc9146ae03e41773404e09f8e6a218`; 2021-01-13 through 2022-07-04 | User-attested own-wallet/microtask origin; date and EUR value explicitly unknown |

The referenced `Buchhaltung Krypto(1).ods` is not present locally; only its
SHA-256-bearing manual transcription is available. No wallet export, faucet or
microtask statement, payment record, or purchase receipt was found.

### Five active gap records

| Gap | Issue ID | Raw record ID | HistoricalTransferLink ID | Resolution ID | Asset / quantity | Target date | Source / basis |
|---|---|---|---|---|---|---|---|
| A | `f5851cf7e9ec4cb79f768bf304e1f24c` | `cd7f45dd92984287974bfff05cee117a` | `1b835d31bf9148b1887ead2dff544f16` | `ffe122370868454c8d81808b5df716c9` | DOGE `118.19411945` | 2020-12-22 transfer | `SELF_TRANSFER`, `PARTIAL`, `original_exchange` |
| B | `0eb844212cc24482ad38ca476e93fee1` | `94810774d61b47e294bdfd1359008067` | `d36b4dfb7189499a9e38b73cfa3a2da4` | `f21c36d9053c43c2ae8bdd27d946717c` | LTC `0.00406235` | 2020-12-22 transfer | `SELF_TRANSFER`, `PARTIAL`, `original_exchange` |
| C | `bba946f224a546cf9a5db93e069b4351` | `9b8eee860db54f8ebef586fc839ced20` | `1cf4ac56f19f438bb4a623adb2b1b684` | `85a31c439eb14075ac674f86c0a490cc` | DOGE `318.65944000` | 2021-01-13 01:07:27 UTC | `SELF_TRANSFER`, `PARTIAL`, `user_attestation` |
| D | `b85b04ab32eb4cbca329eb4532dbcdf6` | `8efa70acf92c4bbb89acfb562177b692` | `a7ba54b09b8d4194beed498bd7eb9bd6` | `b5a776b996244182b61d37a422134afd` | LTC `0.0062935900` | 2021-02-01 15:21:32 UTC | `SELF_TRANSFER`, `PARTIAL`, `user_attestation` |
| E | `7fa193d09d244c528ec34050f0bdf06a` | `dac7b2a680ee49a0bb0bd31eabafef1b` | `a942e7ceaf04444fbe7ad94dd1c3059b` | `c171b5ac26584635a8ee7be2def3148c` | BTC `0.0003000200` | 2022-07-04 11:03:07 UTC | `SELF_TRANSFER`, `PARTIAL`, `user_attestation` |

All five issues were created by completed `historical-transfer-v1` runs. A/B
were created at `2026-10-06 20:13:08 UTC`; C/D/E at
`2026-10-06 21:05:52 UTC`. The active projection intentionally keeps all five
audit gaps active.

### Per-gap forensic findings

#### A – DOGE 118.19411945

- The Bittrex balance before transfer was `11404.55352573 DOGE`.
- Two Bittrex buy records prove `841.39125000 + 10444.96815628 =
  11286.35940628 DOGE` (2018-02-10 and 2020-07-08).
- The residual `118.19411945 DOGE` is a mathematical balance remainder, not a
  proven microtask, faucet, reward, or other economic origin.
- The 2020-12-22 transfer amount and separate 2 DOGE fee are documented, but
  the residual's receipt date and EUR value are absent. The later DOGE
  attestation does not apply retroactively.
- Classification: transfer `SOURCE_PROVEN`; residual origin `SOURCE_PARTIAL`;
  basis coverage `PARTIAL`. No acquisition window narrower than “present no
  later than 2020-12-22” is defensible; EUR basis is unknown.

#### B – LTC 0.00406235

- One Bittrex buy proves `1.48860041 LTC` on 2018-02-25.
- The documented pre-transfer balance was `1.49266276 LTC`; the exact residual
  is `0.00406235 LTC`.
- The 2020-12-22 transfer and 0.01 LTC fee are documented. No record proves
  whether the residual was a microtask, faucet, reward, or another old inflow.
- Classification: transfer `SOURCE_PROVEN`; residual origin `SOURCE_PARTIAL`;
  basis coverage `PARTIAL`. The only safe time bound is present by
  2020-12-22; EUR basis is unknown. The later LTC attestation is unrelated.

#### C – DOGE 318.65944000

- The exact Kraken deposit is present in the ledger and the exact amount is
  repeated in `historical-user-attestation.csv`.
- The attestation says own wallet, microtask, `user_attested`; acquisition
  timestamp, EUR value, and basis are explicitly unknown.
- Local ledger context shows the preceding DOGE deposit on 2020-12-22 and
  later Kraken trades, but no external withdrawal, wallet transaction, or
  receipt record for this amount. Amount equality with the attestation is
  `SOURCE_PROVEN` for the asserted transfer quantity only; economic origin is
  `USER_ATTESTED`, not independently proven.
- The possible acquisition window is only before 2021-01-13; the transfer date
  must not become an acquisition date. No EUR basis is known.

#### D – LTC 0.0062935900

- The exact Kraken deposit and exact attestation quantity match.
- The attestation says own wallet and probable microtask origin, but timestamp,
  EUR value, and basis are unknown.
- Local LTC ledger context contains only the prior 2020-12-22 deposit and this
  deposit; no matching external withdrawal, wallet record, or receipt exists.
- Classification: transfer amount `SOURCE_PROVEN`; economic origin `PROBABLE`
  and `USER_ATTESTED`; acquisition window only before 2021-02-01; EUR basis
  unknown. It is not the 2020 LTC residual.

#### E – BTC 0.0003000200

- The exact Kraken deposit and exact attestation quantity match.
- The attestation says own wallet and probable microtask origin, with timestamp,
  EUR value, and basis unknown.
- No local source contains a matching withdrawal or wallet transaction. The
  documented Bitcoin.de 0.50000000 BTC withdrawal in 2021 and later 2025
  return transfers are separate quantities and cannot explain this 2022
  deposit. Classification: transfer amount `SOURCE_PROVEN`; economic origin
  `PROBABLE`/`USER_ATTESTED`; EUR basis unknown.

### Result matrix

| Gap | Asset | Quantity | Target/context date | Known economic origin | Origin confidence | Known acquisition timestamp | Possible acquisition window | EUR basis known? | Existing source evidence | User attestation | Can close basis gap now? | Missing evidence | Recommended next action |
|---|---|---:|---|---|---|---|---|---|---|---|---|---|---|
| A | DOGE | 118.19411945 | 2020-12-22 | Unknown residual before Bittrex transfer | SOURCE_PARTIAL | No | Before 2020-12-22 | No | Two Bittrex buys plus documented transfer/fee; no residual source record | No | POSSIBLY_WITH_ADDITIONAL_SOURCE | Residual deposit/receipt record with date and EUR value | Search old Bittrex deposit/export, wallet, faucet/microtask, or payment evidence |
| B | LTC | 0.00406235 | 2020-12-22 | Unknown residual before Bittrex transfer | SOURCE_PARTIAL | No | Before 2020-12-22 | No | One Bittrex buy plus documented transfer/fee; no residual source record | No | POSSIBLY_WITH_ADDITIONAL_SOURCE | Residual inflow record with date and EUR value | Search old Bittrex deposit/export, wallet, faucet/microtask, or payment evidence |
| C | DOGE | 318.65944000 | 2021-01-13 01:07:27 UTC | Microtask, user-attested | USER_ATTESTED | No | Before 2021-01-13 | No | Exact Kraken deposit and attestation quantity only | Yes | POSSIBLY_WITH_USER_ATTESTATION | Receipt date and defensible historical EUR value | Obtain a dated, valued user attestation or independent wallet/service record |
| D | LTC | 0.0062935900 | 2021-02-01 15:21:32 UTC | Probable microtask, user-attested | PROBABLE | No | Before 2021-02-01 | No | Exact Kraken deposit and attestation quantity only | Yes | POSSIBLY_WITH_USER_ATTESTATION | Receipt date and defensible historical EUR value | Obtain a dated, valued user attestation or independent wallet/service record |
| E | BTC | 0.0003000200 | 2022-07-04 11:03:07 UTC | Probable microtask, user-attested | PROBABLE | No | Before 2022-07-04 | No | Exact Kraken deposit and attestation quantity only; 0.5 BTC migration excluded | Yes | POSSIBLY_WITH_USER_ATTESTATION | Receipt date and defensible historical EUR value | Obtain a dated, valued user attestation or independent wallet/service record |

No gap can be closed from the currently available local evidence. A/B are the
best candidates for additional-source acquisition because the transfer nature
and known purchase lots are strong, while C/D/E require a defensible original
receipt date and valuation before any historical lot could be considered.
Transfer dates must never be persisted as acquisition dates, and no zero-cost
or estimated basis is acceptable.

### Targeted user questions

1. **Gap A (DOGE 118.19411945):** Stammen diese DOGE, die bereits vor dem
   22.12.2020 auf Bittrex lagen, ebenfalls aus Microtasks/Faucets oder einem
   anderen alten Zufluss, und gibt es dafür noch einen datierten Beleg oder
   Wallet-/Bittrex-Export mit historischem Wert?
2. **Gap B (LTC 0.00406235):** Gibt es für den kleinen LTC-Restbestand neben
   dem Kauf-Lot einen konkreten alten Zuflussbeleg mit Datum und EUR-Wert?
3. **Gaps C–E:** Kannst du für jeden einzelnen Microtask-Zufluss einen
   belastbaren ursprünglichen Receipt-Zeitpunkt und historischen EUR-Wert
   belegen, ohne den Kraken-Depositzeitpunkt zu verwenden?

### Handoff

All five `historical_cost_basis_gap` cases remain open and fail-closed. No
repository files were changed on main, no production data was mutated, and no
import, transformation, TaxCalculationRun, review decision, provider call, or
order occurred. This forensic report is published only on `codex-status`.

## Sprint 5B.3.11E – Controlled production deploy

Deployment completed successfully.

- Deployed code SHA: `93271e0fad88fbba943d46acad90db12a6030453`
- Final main/origin-main SHA after required documentation update:
  `2a48f68d033d4a5ffbd4d51b535d1e129b242ae7`
- Main fast-forwarded from `3a18ffeb0de19a3147ae03818d89cdf19502b561`.
- GitHub Actions Backend for deployed SHA: SUCCESS
  (`37605443169`)
- GitHub Actions Documentation for final main SHA: SUCCESS
  (`37606166678`)

The production preflight confirmed revision
`0013_historical_transfer_evidence`. A fresh SQLite `Connection.backup()` was
created at:

`/home/lo/Backups/kraken-tax-companion/production-5b3.11/5b.3.11e-pre-deploy-20261007-121241.db`

SHA-256:
`715d9fb423c20139c9b14b8b58bbf1e357988089431592b5cfd7809d2394db3f`

The backend was built from the approved main code and started without any
migration, import, transformation, TaxCalculationRun, FinancialReviewResolution,
provider call, or order. Health and read-only smoke checks passed; the backend
container is healthy and the frontend is reachable.

Post-deploy backup, also made with `Connection.backup()`, was:

`/home/lo/Backups/kraken-tax-companion/production-5b3.11/5b.3.11e-post-deploy-121428.db`

SHA-256 is identical to the pre-deploy backup:
`715d9fb423c20139c9b14b8b58bbf1e357988089431592b5cfd7809d2394db3f`.
Both integrity checks returned `ok`.

Final production counts are unchanged:

- RawImportRecords: 3106
- HistoricalTransferLinks / Resolutions: 11 / 11
- Resolution statuses: 6 RESOLVED, 5 PARTIAL
- AcquisitionLots / DisposalEvents / FeeEvents / TradeExecutions: 2711 / 13 / 49 / 39
- ValuationRequirements: 2759
- FinancialReviewResolutions / TaxCalculationRuns: 2 / 2

The projection now reports zero active `ledger_deposit_requires_review` cases
and five active `historical_cost_basis_gap` cases. Other existing review cases
remain visible. No database repair or write-side operation was performed.

Production data was not changed fachlich; only the approved read-side
projection code was deployed. The backend remains free of any import or
transformation repair process.

## Sprint 5B.3.11D – Final CI portability pass

Final review HEAD:

`93271e0fad88fbba943d46acad90db12a6030453`

The Alembic CWD blocker was fixed only in
`backend/tests/test_migration.py`. A shared test helper derives
`BACKEND_ROOT` from `__file__`, loads `backend/alembic.ini` by absolute path,
and sets `script_location` to the absolute `backend/alembic` directory. No
production migration logic, migration file, or Active-Review production code
was changed.

Migration tests passed from both contexts:

- repository root: `pytest backend/tests/test_migration.py` — 2 passed
- `backend/`: `pytest tests/test_migration.py` — 2 passed

The exact repository-root CI command passed: 682 tests, 100% coverage. Ruff,
Black, Mypy, Markdownlint, Docker Compose validation, and `git diff --check`
also passed.

GitHub Actions Backend for this HEAD is SUCCESS:

`https://github.com/synchronierer/kraken-tax-companion/actions/runs/37604602716`

No Documentation workflow was triggered by this test-only commit. The latest
Documentation workflow on the review branch, for `58b9240`, is SUCCESS. Main
and `origin/main` remain
`3a18ffeb0de19a3147ae03818d89cdf19502b561`. Production is unchanged and the
backend remains stopped.

## Sprint 5B.3.11D – Final Mypy cleanup status

The requested local type-checking fix was applied and pushed to the review
branch at final HEAD:

`c96124d190f9288bf71737deb9bf53693306dcf2`

It changes only local variable names in
`backend/app/core/transformation.py`: `current_decision` and
`latest_decision` prevent Mypy from conflating `TransformationIssue` and
`TransformationDecision`. No business logic, ordering, or persistent-gap
semantics changed.

Local focused tests (27), the complete backend suite (682), 100% coverage,
Ruff, Black, Mypy, Markdownlint, Docker Compose validation, and diff checking
all passed.

The new GitHub Actions Backend run for this HEAD is **FAILURE**, so the review
must not merge. Both failures are in existing migration tests: CI invokes
`pytest backend` from the repository root, while those tests construct
`Config("alembic.ini")`; Alembic consequently reports `No
'script_location' key found in configuration.` The two failures are:

- `test_domain_migration_up_and_down`
- `test_export_format_migration_backfills_legacy_runs`

The workflow still reached 100% coverage and reported 680 passed, but exited
non-zero. No Documentation workflow run was triggered for `c96124d` because
that commit contains no documentation-path change; the prior Documentation
run for the review branch at `58b9240` succeeded. This is not sufficient to
claim the requested two SUCCESS checks for the new HEAD.

Per instruction, work stops here; no CI/workflow or unrelated production fix
is added.

## Sprint 5B.3.11D – Review hardening cleanup

The review branch `review/5b.3.11d-active-review-supersession` is complete.
Final review HEAD:

`58b92405067e37804a719117ad814a041fabc37a`

The cleanup commit `chore: clean review hardening diff` removed the accidental
`CODEX_STATUS.md` from the review branch and restored
`backend/app/core/transformation.py` byte-for-byte to the previously reviewed
commit `fb04a2a6ba7b52c271bedabd406d0664340686f3`.

Therefore production code is unchanged relative to `fb04a2a6`. The complete
diff from that commit contains only:

- `backend/tests/test_active_review_projection.py`
- `backend/tests/test_sprint_4a4_financial_reviews.py`
- `docs/DEVELOPMENT_STATE.md`

## Tests and quality gates

- Focused active-review/API tests: 27 passed
- Complete backend suite: 682 passed
- Backend coverage: 100.00%
- Ruff: PASS
- Black `--check`: PASS
- `mypy app`: the restored `fb04a2a6` baseline reports its existing three
  type errors in `app/core/transformation.py`; no new errors were introduced
  by the test/documentation diff
- Markdownlint: PASS
- `docker compose config -q`: PASS
- `git diff --check`: PASS

The hardening tests cover same-code supersession, conflict/domain-event
supersession, independent raw records, deterministic decision ordering, API
and dashboard projections, suggestion suppression, historical audit retention,
and persistent `historical_cost_basis_gap` behavior.

## Repository and safety state

- Main and `origin/main` remain
  `3a18ffeb0de19a3147ae03818d89cdf19502b561`.
- The review branch was pushed; main was not changed or pushed.
- No production database was mutated.
- No deployment, migration, Historical Import, TaxCalculationRun, review
  decision, provider/Kraken call, or order occurred.
- Backend remains stopped.

## Next step

Review and merge the review branch only after accepting the documented Mypy
baseline. Keep production writer-stopped until a separately authorized
deployment.
