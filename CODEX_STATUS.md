# Codex Status

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
