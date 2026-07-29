# ADR 0002: Domain Model Foundation

- Status: Accepted
- Date: 2026-07-28

## Context

Long-lived tax-oriented software must separate original evidence, neutral
economic facts, tax interpretations, and presentation. Framework coupling in
the domain would make rules harder to test and persistence harder to replace.

## Decision

The system follows this dependency flow:

```text
Raw Layer -> Domain Layer -> Tax Layer -> Presentation Layer
```

Sprint 2A implements framework-independent domain dataclasses and maps them
imperatively in the SQLAlchemy infrastructure layer. Repository and unit-of-work
protocols belong to inward-facing ports. Services receive dependencies
explicitly. No tax, FIFO, recommendation, or exchange behavior is included.

IDs come from a central `IdGenerator`. Python 3.12 has no stable standard-library
UUIDv7 implementation, so the current adapter generates UUIDv4. Consumers do
not depend on that choice, allowing a later UUIDv7 adapter and migration plan.

## Consequences

- Domain code imports no web or persistence framework.
- SQLAlchemy mappings and migrations may evolve without changing entity APIs.
- UUIDv7 adoption requires changing an adapter, not every entity constructor.
- API representations remain separate from domain and database models.
