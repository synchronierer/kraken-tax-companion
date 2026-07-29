# API

## Principles

Public capabilities are specified before exposure. Domain entities are not
automatically API resources, and persistence details never leak into handlers.

## Versioning

Future domain endpoints require an explicit versioning policy before release.

## Media Types

JSON is expected. Decimal and UTC serialization contracts must be documented
before domain endpoints are introduced.

## Authentication

No authenticated domain endpoint exists in Sprint 2A.

## Error Model

## Pagination

## Idempotency

Future imports must define idempotency using import sessions and content hashes.

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

FastAPI publishes the health contract. Domain schemas are intentionally absent.

## Compatibility

The Sprint 2A persistence schema does not change the public API.
