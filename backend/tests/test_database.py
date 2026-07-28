from sqlalchemy.orm import Session

from app.database.base import Base
from app.database.session import get_session


def test_metadata_starts_without_application_tables() -> None:
    assert Base.metadata.tables == {}


def test_session_dependency_provides_session() -> None:
    dependency = get_session()

    session = next(dependency)

    assert isinstance(session, Session)

    try:
        next(dependency)
    except StopIteration:
        pass
    else:
        raise AssertionError("Session dependency must yield exactly once.")
