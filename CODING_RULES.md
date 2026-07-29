# Coding Rules

These rules apply to every contribution to Kraken Tax Companion.

## Quality and Types

- Python code is fully typed. MyPy runs in strict mode.
- Ruff, Black, MyPy, tests, and all applicable project checks must pass.
- New domain behavior requires tests. A change must not reduce test coverage.
- Documentation and quality checks stay aligned with implementation.
- Checks must never be disabled or weakened to hide an error.

## Financial Values and Time

- Money, exchange rates, and quantities use `Decimal` exclusively.
- Binary floating-point values are forbidden in financial or tax logic.
- Timestamps are timezone-aware and normalized to UTC.
- Conversions at system boundaries must be explicit and tested.

## Architecture and Persistence

- The domain is framework-free.
- SQLAlchemy belongs exclusively to the Infrastructure layer.
- Business logic does not belong in routers, ORM mappings, or the UI.
- Repositories and units of work isolate persistence from domain behavior.
- Database schema changes are made exclusively through Alembic migrations.
- Material architecture decisions require an Architecture Decision Record
  (ADR) in `docs/adr/`.

## Security and Data

- Never commit secrets, access tokens, private keys, or credentials.
- Never use real tax records or real user data in tests, examples, fixtures, or
  documentation.
- Logs and error messages must not leak sensitive payloads.

## Contributions

- Keep changes focused, auditable, and documented.
- Use Conventional Commits.
- Update tests and documentation with behavior or architecture changes.
- Run the relevant checks described in [CONTRIBUTING.md](CONTRIBUTING.md)
  before submitting a change.
