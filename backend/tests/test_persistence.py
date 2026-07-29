from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.core.entities import EarnLot, ImportSession, ImportStatus, RawImportRecord
from app.core.identifiers import new_id
from app.database.base import Base


def test_decimal_and_utc_round_trip() -> None:
    models.configure_mappings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    imported = ImportSession(
        source="file", version="1", status=ImportStatus.COMPLETED, started_at=now
    )
    lot = EarnLot(
        lot_id=new_id(),
        coin="BTC",
        quantity=Decimal("1.123456789012345678"),
        occurred_at=now,
        import_session_id=imported.id,
    )

    with Session(engine) as database:
        database.add_all([imported, lot])
        database.commit()
        database.expire_all()
        stored = database.scalar(select(EarnLot))
        assert stored is not None
        assert stored.quantity == Decimal("1.123456789012345678")
        assert stored.occurred_at.tzinfo is UTC


def test_immutable_records_reject_updates() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    imported = ImportSession(
        source="file", version="1", status=ImportStatus.COMPLETED, started_at=now
    )
    record = RawImportRecord(
        import_session_id=imported.id,
        source="file",
        content_hash="sha256:1234",
        payload={"immutable": True},
    )

    with Session(engine) as database:
        database.add_all([imported, record])
        database.commit()
        record.source = "changed"
        with pytest.raises(ValueError, match="Immutable"):
            database.commit()
