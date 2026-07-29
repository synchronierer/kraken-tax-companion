# Architecture

## Context

Kraken Tax Companion preserves evidence and explains every derived result.
Sprint 2A establishes data boundaries without exchange, tax, FIFO, or
recommendation behavior.

## System Overview

```text
Raw Layer -> Domain Layer -> Tax Layer -> Presentation Layer
```

The Raw Layer preserves external evidence. The Domain Layer represents neutral
economic facts. Future layers may consume those facts but never mutate sources.

## Backend

### API Layer

FastAPI is the transport boundary. Dependency construction belongs to the
composition root; domain entities remain unaware of HTTP.

### Application Services

Services receive repositories, a unit-of-work factory, and infrastructure
ports explicitly. Sprint 2A defines those boundaries without use-case logic.

### Domain Modules

Framework-independent dataclasses define the seven foundational entities.
Creation validates Decimal amounts, required text, and aware UTC timestamps.

### Infrastructure

SQLAlchemy maps domain classes imperatively. Repository protocols point inward;
concrete adapters will arrive with their use cases.

## Frontend

### Application Shell

The React shell remains a presentation-only consumer.

### Routing

Routes remain placeholders during Sprint 2A.

### API Integration

No domain endpoint is exposed. `GET /health` remains the sole route.

## Persistence

### SQLite

UUIDs use SQLAlchemy's portable type. Decimal amounts use canonical strings to
avoid binary floating-point conversion. Reads restore UTC awareness.

### PostgreSQL Migration Path

The Decimal adapter selects `NUMERIC(38,18)` for PostgreSQL. Portable UUID,
JSON, foreign-key, and constraint definitions avoid SQLite-specific logic.

### Alembic

Revision `0001_domain_foundation` creates seven empty tables and is tested in
both directions. It contains neither seed data nor behavior.

## Cross-Cutting Concerns

### Configuration

Runtime configuration comes exclusively from environment values.

### Logging

One application logging configuration remains the operational boundary.

### Security

Raw payloads are sensitive evidence. No credentials or exchange client exists.

### Testing

Tests cover invariants, exact persistence, UTC, immutability, and migrations.

## Dependency Rules

- Presentation depends on application interfaces.
- Application services depend on domain ports.
- Persistence depends on domain entities, never the reverse.
- Raw evidence may produce facts; facts never rewrite evidence.
- The future Tax Layer may only consume Domain Layer facts.

## Deployment

Docker Compose continues to run the backend and frontend with persistent data,
log, and export volumes.
