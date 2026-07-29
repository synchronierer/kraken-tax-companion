# ADR 0004: Decimal Amounts and UTC Time

- Status: Accepted
- Date: 2026-07-28

## Context

Binary floating-point values cannot represent many financial decimals exactly.
Naive or local timestamps are ambiguous and undermine event ordering.

## Decision

All coin quantities and monetary prices are Python `Decimal` values. Domain
constructors reject other numeric types. PostgreSQL persists them as
`NUMERIC(38,18)`. SQLite uses canonical decimal strings because its numeric
affinity can convert high-precision values through binary floating point.

Every domain timestamp must be timezone-aware. Values are normalized to UTC at
the domain boundary. Persistence strips the zone only for SQLite storage and
restores explicit UTC awareness when reading.

## Consequences

- Decimal precision survives SQLite round trips exactly.
- No float may enter financial domain constructors.
- Local and naive timestamps fail validation.
- API serialization must later specify Decimal strings and UTC timestamps.
