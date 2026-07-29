# ADR 0008: Generic Batch Import Contract

- Status: Accepted
- Date: 2026-07-29

## Context

The original generic import implementation treated one JSON object as one
artifact. A source-neutral engine also needs ordered multi-record artifacts,
typed outcomes, retry semantics, and lifecycle audit evidence without assuming
CSV columns or a particular provider.

## Decision

`ImportContext` separates identity from description. Source, contract version,
logical source name, and explicitly supplied identity data form its comparable
identity. Receipt time, actor, correlation ID, session, and user metadata are
descriptive provenance and never enter the content hash.

A batch hash is SHA-256 over an ordered stream. It starts with the ASCII domain
separator `kraken-tax-companion:records:v1\n`; every record contributes its
canonical-JSON byte length, a colon, its canonical UTF-8 bytes, and a newline.
Object keys are sorted recursively, array and record order remain significant,
and Unicode values and line endings inside strings are preserved. The hasher
accepts an iterable and updates incrementally.

Import idempotency uses `(source, import_hash)`. A completed attempt is returned
as a typed duplicate result. A failed attempt is also considered registered by
default; `retry_failed=True` is the only explicit retry mechanism. Retry creates
a new session and never reuses or mutates failed evidence. Checks and writes
share one unit of work. SQLite serializes writers; a future PostgreSQL rollout
must add an atomic claim/advisory-lock strategy before concurrent import
workers are enabled.

Sessions follow the existing central state machine. They additionally retain
the batch hash and a bounded error summary. Invalid and repeated transitions
raise the typed `InvalidStateTransitionError`. Raw records carry a zero-based
sequence, optional external ID, original JSON payload, and separate technical
metadata.

Expected validation and duplicate outcomes are represented by `ImportResult`.
Structured issues distinguish technical, validation, transformation, and
persistence categories. Infrastructure exceptions are translated at the
application boundary. Audit events record creation, start, completion,
failure, and duplicate detection.

## Consequences

- Provider adapters can stream canonical records without provider fields in
  the engine.
- Descriptive timestamps, UUIDs, and metadata cannot change content identity.
- Reordered records intentionally produce a different import hash.
- Repeated payloads within one batch are valid because position identifies raw
  records.
- Concurrent PostgreSQL workers remain disabled until an atomic idempotency
  claim is implemented and tested.
