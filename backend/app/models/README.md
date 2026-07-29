# Models

Registers the imperative SQLAlchemy mappings for framework-independent domain
entities. The metadata contains the Sprint 2A tables and is the source for
Alembic schema comparison. Importing this package configures mappings exactly
once; application behavior must remain outside this module.
