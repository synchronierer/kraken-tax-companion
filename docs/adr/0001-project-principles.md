# ADR 0001: Project Principles and Technology Baseline

- Status: Accepted
- Date: 2026-07-28

## Context

Kraken Tax Companion needs an approachable self-hosted architecture that keeps
financial transformations explainable, testable, and portable. The first
commit establishes constraints before domain logic is introduced.

## Decision

### Why FastAPI

FastAPI provides typed request and response contracts, standards-based OpenAPI
output, dependency injection, and a small application surface. Its alignment
with Python type annotations supports API-first development and reviewable
interfaces.

### Why React

React has a mature component ecosystem and supports a clear separation between
API behavior and presentation. TypeScript, Material UI, and React Router give
the project accessible primitives, strict contracts, and explicit navigation
without creating a custom design system in the foundation sprint.

### Why SQLite First

SQLite makes local and self-hosted startup predictable and requires no
separate database service. SQLAlchemy and Alembic isolate database access and
schema evolution so PostgreSQL can later be adopted without rewriting domain
logic. Database-specific behavior is prohibited unless documented by a new
decision record.

### Why Audit First

Tax-oriented results are useful only when users can reproduce and explain
them. Provenance, immutable source records, explicit rules, and linked
corrections therefore shape the data model and every use case from the start.

### Why No Automatic Sales Logic

Executing a sale introduces financial risk, credential exposure, regulatory
complexity, and irreversible external effects. The project may explain and
present recommendations for human review, but it will not submit trades or
hold credentials capable of doing so.

## Consequences

- Public behavior is designed and documented as an API.
- Original imports remain immutable.
- Domain decisions require deterministic rules and audit evidence.
- Framework and persistence details stay outside the domain.
- Recommendations are informational and require explicit human action.
- A future database change requires compatibility tests and a migration plan.
