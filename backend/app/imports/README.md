# Imports

Owns source-neutral validation, canonical hashing, idempotency, and immutable
raw JSON ingestion. `ImportService` orchestrates the pipeline through explicit
repository and unit-of-work ports. Original records retain provenance and are
never silently rewritten.

This module contains no transport adapter, provider-specific behavior, domain
transformation, or public API.
