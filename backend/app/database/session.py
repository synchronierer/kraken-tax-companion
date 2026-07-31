from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config.settings import get_settings
from app.database.mappings import configure_mappings

configure_mappings()
engine = create_engine(get_settings().database_url)
SessionFactory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    with SessionFactory() as session:
        yield session
