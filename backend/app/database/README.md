# Database

Owns SQLAlchemy engine, sessions, declarative metadata, and migration
integration. Domain code depends on abstractions rather than this module.

Imperative mappings keep SQLAlchemy outside the domain. `UtcDateTime` preserves
aware UTC semantics across SQLite, while `ExactDecimal` stores canonical strings
on SQLite and `NUMERIC(38,18)` on PostgreSQL. Immutable record mappings reject
updates during flush.
