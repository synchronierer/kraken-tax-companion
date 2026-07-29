# Tax Model

## Scope

Sprint 2A defines neutral records only. It contains no tax calculation,
jurisdiction rule, FIFO allocation, classification, or recommendation.

## Terminology

- Import session: provenance envelope for one future ingestion operation.
- Raw import record: unchanged payload plus source and content hash.
- Earn lot: neutral receipt candidate with quantity and UTC time.
- Sale: neutral disposal candidate without allocation or interpretation.
- Price snapshot: sourced EUR observation without valuation logic.
- Audit event: append-only evidence for an attributable system action.

## Source Facts

`RawImportRecord` is the boundary for original external facts. JSON payloads are
linked to an import session. A content hash supports later integrity checks. No
Kraken-specific schema exists in this sprint.

## Derived Facts

Domain records may later be derived from raw evidence. Provenance is explicit,
and derivation must eventually emit audit evidence.

## Asset Lots

`EarnLot` has a globally unique ID and unique immutable lot ID. Quantities are
positive `Decimal` values. Persisted updates are rejected; correction semantics
will be designed explicitly before use.

## Valuation

`PriceSnapshot` prepares sourced EUR observations. It performs no lookup or
conversion.

## FIFO

FIFO is outside Sprint 2A. `Sale` intentionally has no lot allocation.

## Classification

Tax classification is outside Sprint 2A.

## Jurisdiction Boundaries

The domain model is jurisdiction-neutral and is not tax advice.

## Audit Evidence

Audit events record UTC time, event and entity types, entity ID, user/system
actor, and JSON metadata. No business event catalog is defined yet.

## Test Strategy

Tests verify UUID generation, exact Decimal values, UTC awareness, invariants,
immutable update rejection, persistence round trips, and schema migrations.
