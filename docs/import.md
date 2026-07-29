# Import

## Goals

The import engine preserves external JSON as immutable evidence. It is generic,
transactional, auditable, and independent of any exchange or transport.

Sprint 2B ends at raw persistence. It performs no domain transformation, tax
calculation, FIFO allocation, pricing, or recommendation.

## Supported Sources

The engine accepts UTF-8 JSON objects from internal callers as `str` or
`bytes`. Future file and network integrations will be adapters that translate
their input into this contract. No provider-specific adapter exists yet.

## Import Context

Each run creates an `ImportSession` and immutable `ImportContext` containing:

- source and source version;
- aware UTC receipt time;
- user or system actor;
- correlation ID; and
- the matching import session.

Context construction rejects values that differ from the session.

## State Machine

The normal lifecycle is:

```text
CREATED
  -> RECEIVED
  -> VALIDATING
  -> HASHING
  -> CHECKING_DUPLICATES
  -> PERSISTING
  -> COMPLETED
```

A duplicate transitions from `CHECKING_DUPLICATES` directly to `COMPLETED`.
Active states may end as `FAILED` or `CANCELLED`. The allowed-transition map is
centralized and invalid transitions fail explicitly.

## Validation

The generic validation layer rejects empty input, invalid UTF-8 or JSON, and
non-object JSON roots. `ImportValidator` is a protocol for composable rules.
`RequiredFieldsValidator` supplies source-neutral required-field validation.

No domain or provider-specific rules are included. Unsupported JSON values,
including non-finite numbers, fail canonicalization.

## Canonical Hashing

Hash input is the UTF-8 encoding of canonical JSON with:

- object keys sorted recursively;
- no insignificant whitespace;
- Unicode emitted directly rather than ASCII escapes;
- JSON arrays kept in their original order; and
- non-finite or non-JSON values rejected.

SHA-256 is calculated over those bytes and stored as a lowercase 64-character
hexadecimal string. An optional expected hash is compared case-insensitively.
JSON object key order therefore does not affect identity, while any meaningful
value or array-order change does.

## Idempotency

Identity is the pair `(source, content_hash)`. Before persistence, the engine
checks the raw repository for this pair. An identical repeat:

- creates no `RawImportRecord`;
- creates no additional raw-persistence `AuditEvent`;
- completes its own `ImportSession`; and
- records one received and one skipped item.

A database unique constraint on the same pair provides a final concurrency
guard.

## Immutable Storage

New input is stored once as a `RawImportRecord` containing source, canonical
hash, original parsed JSON payload, import-session reference, and UTC creation
time. ORM update hooks reject later mutation.

## Transactions

Session progress, raw evidence, and its audit event use one SQLAlchemy unit of
work. Errors roll the entire attempt back. A recovery unit of work persists the
failed session and `ImportError` so operational failure evidence survives.

## Audit and Provenance

A newly persisted raw record creates exactly one `raw_import.persisted` event
with entity identity, actor, session, correlation ID, source, and content hash.
Skipped duplicates deliberately do not duplicate that event.

## Error Reporting

`ImportError` records UTC time, session, category, stable error code,
description, original exception summary, and affected record when available.

`ErrorCategory.IMPORT` covers parsing, integrity, validation, I/O, and
infrastructure failures. `ErrorCategory.DOMAIN` and `DomainValidationError`
reserve a separate boundary for future business rules; Sprint 2B does not apply
or persist such rules.

## Security

Raw payloads and exception summaries may contain sensitive information. They
remain persistence data and are not emitted through a public endpoint. The
engine contains no credentials, API keys, signatures, or network client.

## Testing

Tests verify deterministic hashing, key-order independence, content changes,
validation, lifecycle transitions, duplicate suppression, audit creation,
rollback, failure evidence, repositories, unit-of-work behavior, and Alembic
migrations.
