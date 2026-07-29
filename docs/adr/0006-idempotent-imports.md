# ADR 0006: Idempotent Raw Imports

- Status: Accepted
- Date: 2026-07-29

## Context

External deliveries can be retried, replayed, or received concurrently.
Persisting each delivery again would duplicate evidence and audit events,
making later results irreproducible.

## Decision

A raw import is identified by `(source, content_hash)`. `ImportService` checks
that pair through `RawImportRepository` before persistence. A duplicate retains
its own completed `ImportSession` with a skipped counter, but creates neither a
new `RawImportRecord` nor another raw-persistence `AuditEvent`.

The database enforces the same composite uniqueness constraint. The service
check provides normal control flow; the constraint is the final concurrency
guard. Each import attempt uses a SQLAlchemy unit-of-work transaction.

## Consequences

- Safe retries do not multiply evidence or audit history.
- Session history distinguishes received, persisted, and skipped records.
- Identical content from two deliberately different sources remains distinct.
- A future batch contract must define whether identity applies per artifact or
  per contained record.
