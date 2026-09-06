# API

## Sale proposals

- `GET /api/sale-proposals/inventory` returns documented FIFO inventory for the
  read-only planner and keeps exchange availability separate.
- `POST /api/sale-proposals/simulate` performs a stateless FIFO dry-run using an
  explicitly supplied manual reference price.

See [Sprint 5A sale planner](sprint-5a-sale-planner.md) for the safety and tax
semantics.

## Steuerliche Reviewentscheidungen

- `GET /api/tax-review-decisions` listet offene und entschiedene
  Staking-Gebührenprüfungen mit Historie. Filter sind `year`, `status`, `asset`
  und `decision`.
- `POST /api/tax-review-decisions` dokumentiert eine Einzelentscheidung.
- `POST /api/tax-review-decisions/bulk` dokumentiert atomar bis zu 200
  Einzelentscheidungen mit gemeinsamer `batch_id`.

Zulässige Werte sind `include_as_werbungskosten` und
`exclude_from_werbungskosten`. Eine leere Begründung, doppelte IDs, fremde
Reviewarten, supersedierte Bewertungen oder ungeeignete Gebührenbewertungen
werden abgewiesen. Kein Endpunkt startet automatisch einen Taxlauf.

## Principles

Public capabilities are specified before exposure. Domain entities are not
automatically API resources, and persistence details never leak into handlers.

## Current Surface

Sprint 2B exposes no public import endpoint. The generic `ImportService` is an
internal application service and has no HTTP request or response schema.
`GET /health` remains the sole public route.

## Versioning

Future domain or import endpoints require an explicit versioning and
compatibility policy before release.

## Media Types

JSON is expected for future application endpoints. Decimal and UTC
serialization contracts must be documented before domain endpoints are
introduced. The internal import canonicalization contract is documented in
`import.md` and is not an HTTP representation contract.

## Authentication

No authenticated domain or import endpoint exists in Sprint 2B.

## Error Model

`ImportError` is an internal persistence model, not a public response. Any
future API error contract must avoid exposing raw payloads, exception details,
configuration, or secrets.

## Pagination

No collection endpoint exists.

## Idempotency

Internal raw import identity uses `(source, content_hash)`. A future transport
must define how clients supply source identity and optional expected hashes
before an endpoint is exposed.

## Health

### Request

`GET /health`

### Successful Response

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

The endpoint is unauthenticated and reports application liveness. It does not
expose database contents, configuration, or secrets.

## OpenAPI

FastAPI publishes the health contract. Import and domain schemas are
intentionally absent.

## Compatibility

Sprint 2B changes internal persistence and services only; the public API remains
unchanged.
