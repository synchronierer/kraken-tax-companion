# Architecture

## Context

Kraken Tax Companion preserves evidence and explains every derived result.
Sprint 2B adds source-neutral ingestion without exchange, tax, FIFO, or
recommendation behavior.

## System Overview

```text
Raw Layer -> Domain Layer -> Tax Layer -> Presentation Layer
```

The Raw Layer preserves external evidence. The Domain Layer represents neutral
economic facts. Future layers may consume those facts but never mutate sources.
Sprint 2B operates only in the Raw and Audit layers.

## Import Pipeline

```text
External Source
        |
        v
Import Adapter
        |
        v
Raw Import Layer
        |
        v
Validation Layer
        |
        v
Idempotency Layer
        |
        v
Persistence Layer
```

Adapters remain outside the generic engine. `ImportService` orchestrates an
immutable `ImportContext`, validation, canonical hashing, duplicate detection,
raw persistence, audit creation, and session completion. It does not transform
raw records into domain entities.

## Backend

### API Layer

FastAPI is the transport boundary. Dependency construction belongs to the
composition root; domain entities remain unaware of HTTP. Sprint 2B exposes no
import endpoint.

### Application Services

Services receive repositories, validators, clocks, ID generators, and
unit-of-work factories explicitly. `ImportService` is the source-neutral import
use case.

### Domain Modules

Framework-independent dataclasses define domain, raw, audit, session, and error
entities. Creation validates Decimal amounts, required text, counters, and
aware UTC timestamps.

### Infrastructure

SQLAlchemy maps domain classes imperatively. Repository protocols point inward.
Concrete SQLAlchemy repositories and the unit of work implement those ports.

## Import Session Lifecycle

The centralized state machine permits only declared transitions:

```text
CREATED -> RECEIVED -> VALIDATING -> HASHING
        -> CHECKING_DUPLICATES -> PERSISTING -> COMPLETED
```

`COMPLETED` is also permitted directly after duplicate checking. Active states
may transition to `FAILED` or `CANCELLED`. Terminal states reject further
transitions. Every transition updates the aware UTC timestamp; terminal states
also set the end time.

## Transactions and Failure Evidence

A successful or skipped import runs inside one SQLAlchemy unit of work. Any
failure rolls back the attempted session, raw record, and audit event together.
A separate recovery transaction then persists the failed session and
`ImportError`; otherwise the evidence of failure would be lost in that same
rollback.

Import failures and future domain failures are separated by `ErrorCategory`.
Sprint 2B persists only import failures and reserves the domain category and
exception base for later business validation.

## Persistence

### SQLite

UUIDs use SQLAlchemy's portable type. Decimal amounts use canonical strings to
avoid binary floating-point conversion. Reads restore UTC awareness.

Batch idempotency queries session source and SHA-256 inside the unit of work.
Raw records are unique by session and sequence, allowing equal payloads in one
artifact. SQLite's serialized writer model is supported. Concurrent PostgreSQL
import workers remain disabled until ADR 0008's atomic claim is implemented.

### PostgreSQL Migration Path

The Decimal adapter selects `NUMERIC(38,18)` for PostgreSQL. Portable UUID,
JSON, foreign-key, enum, and constraint definitions avoid SQLite-specific
application logic.

### Alembic

Revision `0001_domain_foundation` creates the foundational tables. Revision
`0002_generic_import_engine` introduces the session lifecycle fields,
the original idempotency constraint, and import errors without seed data.
Revision `0003_import_batch_model` adds persistent batch hashes, error
summaries, ordered record metadata, and replaces record-content uniqueness
with session-position uniqueness.

## Frontend

The React shell remains a presentation-only consumer. Routes remain
placeholders and no import interface is added in Sprint 2B.

## Cross-Cutting Concerns

### Configuration and Logging

Runtime configuration comes exclusively from environment values. One
application logging configuration remains the operational boundary.

### Security

Raw payloads and exception details are sensitive evidence. They are kept out of
logs and public APIs. No credentials, provider client, or API keys exist.

### Testing

Tests cover invariants, lifecycle transitions, canonical hashing, validation,
idempotency, rollback, audit creation, exact persistence, and migrations.

## Dependency Rules

- Presentation depends on application interfaces.
- Application services depend on domain ports.
- Persistence depends on domain entities, never the reverse.
- Import adapters depend on the generic import boundary.
- Raw evidence may produce facts later; facts never rewrite evidence.
- The future Tax Layer may only consume Domain Layer facts.

## Deployment

Docker Compose runs the backend and frontend with persistent data, log, and
export volumes.
