# ADR 0003: Immutable Earned Lots and Source Records

- Status: Accepted
- Date: 2026-07-28

## Context

Silent edits to imported evidence or derived lots destroy reproducibility.
Auditability requires historical facts to retain their original identity and
contents.

## Decision

`RawImportRecord`, `EarnLot`, `PriceSnapshot`, and `AuditEvent` are append-only
at the persistence boundary. SQLAlchemy rejects updates during flush. Earn lots
also carry a unique, stable `lot_id` distinct from their record ID. Raw records
retain their source, import-session link, JSON payload, and content hash.

Deletion and correction workflows are deliberately undefined in Sprint 2A.
Future corrections must create explicit linked evidence and audit events rather
than overwrite history.

## Consequences

- Accidental updates fail before SQL is written.
- Future repositories for immutable records expose insertion and reading only.
- Correction semantics require a later reviewed decision record.
- Database administrators remain responsible for restricting direct writes.
