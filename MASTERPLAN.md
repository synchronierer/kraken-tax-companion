# Kraken Tax Companion Master Plan

This document is binding for project direction. Changes require a reviewed
pull request that explains the architectural impact.

## Vision

Kraken Tax Companion gives self-hosting users a transparent, reproducible, and
auditable foundation for understanding Kraken activity in tax-related
workflows. Every derived result must be traceable to immutable imported data
and an explicit rule.

## Why This Project Exists

Users need more than opaque totals. They need provenance, reproducibility, and
clear separation between source records, interpretations, recommendations,
and human decisions. The project exists to make those relationships visible
without claiming to replace professional tax advice.

## Non-Goals

- Providing legal, tax, or investment advice.
- Filing tax returns or guaranteeing regulatory compliance.
- Holding credentials required to execute exchange trades.
- Automatically buying or selling assets.
- Mutating or silently correcting original import records.
- Hiding calculations behind unexplained scores or black-box models.

## Architecture Principles

### Documentation First

Behavior, interfaces, data meaning, and significant decisions are documented
before implementation.

### Audit First

Every meaningful operation has attributable inputs, outputs, timestamps, and
an explainable rule. Auditability is a design constraint, not an add-on.

### API First

Domain capabilities are exposed through versionable interfaces independent of
the frontend.

### Immutable Data

Original imports and historical facts are append-only. Corrections are new,
linked records rather than destructive updates.

### Clean Architecture

Domain rules do not depend on frameworks, storage engines, transport, or user
interfaces. Dependencies point inward.

## Development Rules

- Use Conventional Commits.
- Develop through focused pull requests.
- Require code review before merge.
- Never merge untested commits.
- Update documentation before implementing behavior.
- Keep Python fully typed and TypeScript in strict mode.
- Keep configuration in environment variables.
- Never commit secrets or sensitive financial data.
- Use one structured and consistent logging configuration.
- Do not add unexplained logic, commented-out blocks, or unfinished markers.

## Sprint Planning

### Sprint 1: Repository

Establish the professional, runnable, documented project foundation.

### Sprint 2: Import

Import Kraken source data without altering it and record provenance.

### Sprint 3: Tax Journal

Build the reviewable journal and explicit classification workflow.

### Sprint 4: FIFO

Implement a documented, deterministic, and tested FIFO calculation.

### Sprint 5: Recommendation Engine

Produce explainable suggestions from explicit rules and documented inputs.

### Sprint 6: Sales Dialog

Support human review and recording of sales decisions without trade execution.

### Sprint 7: Home Assistant

Expose privacy-conscious status and notification integration.

### Sprint 8: Release 1.0

Harden security, documentation, migration behavior, and release operations.

## Quality Standard

Maintainability, correctness, traceability, security, and documentation take
priority over delivery speed. A feature is complete only when its behavior is
typed, tested, documented, observable, and reviewable.
