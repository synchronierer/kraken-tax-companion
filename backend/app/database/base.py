from sqlalchemy.orm import registry

mapper_registry = registry()


class Base:
    """Compatibility facade exposing the application's SQLAlchemy metadata."""

    metadata = mapper_registry.metadata
