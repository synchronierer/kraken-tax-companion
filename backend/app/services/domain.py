from collections.abc import Callable
from dataclasses import dataclass

from app.core.identifiers import IdGenerator
from app.core.unit_of_work import UnitOfWork


@dataclass(frozen=True)
class ServiceDependencies:
    """Explicit dependencies available to future application services."""

    unit_of_work_factory: Callable[[], UnitOfWork]
    id_generator: IdGenerator
