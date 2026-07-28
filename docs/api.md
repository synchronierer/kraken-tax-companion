# API

## Principles

## Versioning

## Media Types

## Authentication

## Error Model

## Pagination

## Idempotency

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

## Compatibility
