from dataclasses import dataclass

from app.config.settings import Settings, get_settings
from app.core.identifiers import IdGenerator, Uuid4IdGenerator


@dataclass(frozen=True)
class DependencyContainer:
    """Application composition root for replaceable infrastructure."""

    settings: Settings
    id_generator: IdGenerator


def build_container() -> DependencyContainer:
    return DependencyContainer(
        settings=get_settings(),
        id_generator=Uuid4IdGenerator(),
    )
