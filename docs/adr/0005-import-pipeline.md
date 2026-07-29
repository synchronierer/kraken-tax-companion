# ADR 0005: Generic Import Pipeline

- Status: Accepted
- Date: 2026-07-29

## Context

External sources differ in transport and format. Coupling ingestion to one
provider would mix evidence capture, domain transformation, and infrastructure,
making imports difficult to reproduce and audit.

## Decision

Imports use this source-neutral pipeline:

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

`ImportService` owns orchestration. An immutable `ImportContext` carries the
session, source, source version, received time, actor, and correlation ID.
Adapters will provide raw JSON but remain outside the engine. The engine never
creates domain or tax objects.

The primary path runs in one SQLAlchemy unit-of-work transaction. If it fails,
that transaction is rolled back. A separate recovery transaction records the
failed session and its import error, because failure evidence must survive the
rollback of the attempted import.

## Consequences

- Future file and network adapters share one ingestion contract.
- Raw evidence is isolated from future domain transformations.
- Transaction boundaries and dependencies are explicit and testable.
- Adapter-specific parsing and business validation remain future work.
