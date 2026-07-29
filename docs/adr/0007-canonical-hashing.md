# ADR 0007: Canonical JSON Hashing

- Status: Accepted
- Date: 2026-07-29

## Context

Raw JSON objects can be semantically identical while differing in key order or
insignificant whitespace. Hashing their received byte representation would
mistake formatting changes for new evidence.

## Decision

The engine parses one non-empty UTF-8 JSON object, then serializes it with
recursively sorted object keys, compact separators, direct Unicode output, and
unchanged array order. Non-finite numbers and values outside the JSON data model
are rejected. SHA-256 is calculated over the canonical UTF-8 bytes and stored
as a lowercase hexadecimal digest.

An optional caller-provided digest is normalized to lowercase and checked
before persistence. Canonicalization applies to identity; the parsed original
payload remains the immutable evidence representation.

## Consequences

- Object key order and insignificant input whitespace do not change identity.
- Meaningful value changes and array reordering do change identity.
- Hashes are deterministic and reproducible across supported callers.
- Changing the canonicalization contract would require explicit versioning and
  migration planning.
