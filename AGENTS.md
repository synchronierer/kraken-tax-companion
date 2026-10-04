# Kraken Tax Companion – Agent Instructions

## Mandatory startup

Before changing code in this repository:

1. Read `docs/DEVELOPMENT_STATE.md`.
2. Read `MASTERPLAN.md`.
3. Read `CODING_RULES.md`.
4. For development workflow details, consult `CONTRIBUTING.md`.
5. Inspect `git status` and the current HEAD before making changes.
6. Treat the working tree and repository state as authoritative if documentation
   and code disagree; report the discrepancy before changing behavior.

Do not start implementation until the current sprint, safety constraints, last
validated state, and next intended step from `docs/DEVELOPMENT_STATE.md` are
understood.

## Mandatory handoff maintenance

After a meaningful development milestone, completed sprint, important
investigation, changed safety constraint, changed data migration state, or
before handing work to another session:

- update `docs/DEVELOPMENT_STATE.md`;
- keep it concise and current;
- remove superseded operational detail instead of endlessly appending;
- record the exact commit/working-tree state;
- record what was actually validated;
- record the next intended step;
- never add secrets or real private financial data.

A feature is not considered handed off cleanly until the development-state
document reflects reality.

## Safety invariants

Always preserve these constraints unless the user explicitly changes them:

- Never expose API keys or `.env` contents.
- Never perform a real Kraken write/trading operation without explicit authorization.
- Never create an order automatically.
- Never start a new TaxCalculationRun automatically.
- Never make review decisions automatically.
- Never perform a production Historical Import without explicit authorization.
- Historical reconciliation must pass offline gates before production mutation.
- Use `sqlite3.Connection.backup()` for live SQLite backups; never copy a
  running SQLite DB byte-for-byte.
- Financial values use Decimal.
- Times are timezone-aware UTC.
- Fail closed when historical classification evidence is ambiguous.

## Development workflow

Default sequence for behavior changes:

1. understand current state;
2. implement;
3. focused tests;
4. full project checks;
5. real-data OFFLINE sandbox validation where applicable;
6. inspect diff/status;
7. commit;
8. deploy only after explicit validation;
9. for deploys involving persistent data, verify pre/post logical DB integrity
   as appropriate;
10. update `docs/DEVELOPMENT_STATE.md`.

Do not weaken checks to obtain a pass.

## Documentation hierarchy

Use these sources in this order for their respective purpose:

- `MASTERPLAN.md`: product direction and architectural principles.
- `CODING_RULES.md`: permanent engineering rules.
- `docs/DEVELOPMENT_STATE.md`: current implementation/handoff state.
- `CONTRIBUTING.md`: development commands/workflow.
- ADRs: durable architecture decisions.
- Git history: exact implemented history.

`docs/DEVELOPMENT_STATE.md` is a status document, not a replacement for
permanent architecture documentation.
