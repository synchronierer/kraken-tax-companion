from typing import Protocol
from uuid import UUID, uuid4


class IdGenerator(Protocol):
    """Generate globally unique identifiers behind a replaceable boundary."""

    def new(self) -> UUID:
        """Return a new identifier."""


class Uuid4IdGenerator:
    """Standard-library fallback until UUIDv7 is stable in the runtime."""

    def new(self) -> UUID:
        return uuid4()


_default_generator: IdGenerator = Uuid4IdGenerator()


def new_id() -> UUID:
    return _default_generator.new()
