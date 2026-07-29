from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    Configuration,
    EarnLot,
    ImportSession,
    ImportStatus,
    PriceSnapshot,
    RawImportRecord,
    Sale,
    positive_decimal,
    required_text,
)
from app.core.identifiers import Uuid4IdGenerator, new_id
from app.core.time import require_utc, utc_now

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def import_session() -> ImportSession:
    return ImportSession(
        source="exchange-export",
        version="1",
        status=ImportStatus.CREATED,
        started_at=NOW,
        correlation_id=new_id(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )


def test_domain_entities_use_uuid_decimal_and_utc() -> None:
    session = import_session()
    lot = EarnLot(
        lot_id=new_id(),
        coin="btc",
        quantity=Decimal("1.250000000000000001"),
        occurred_at=NOW,
        import_session_id=session.id,
    )
    sale = Sale(
        coin="eth",
        quantity=Decimal("0.5"),
        occurred_at=NOW,
        import_session_id=session.id,
    )
    price = PriceSnapshot(
        coin="btc", captured_at=NOW, price_eur=Decimal("98765.43"), source="price-file"
    )
    audit = AuditEvent(
        occurred_at=NOW,
        event_type="entity.recorded",
        entity_type="EarnLot",
        entity_id=lot.id,
        actor_type=AuditActorType.SYSTEM,
        actor_id="import-worker",
        metadata={"source": "file"},
    )
    raw = RawImportRecord(
        import_session_id=session.id,
        source="exchange-export",
        content_hash="sha256:1234",
        payload={"record": 1},
    )
    configuration = Configuration()

    for entity in (session, lot, sale, price, audit, raw, configuration):
        assert isinstance(entity.id, UUID)
    assert lot.quantity == Decimal("1.250000000000000001")
    assert sale.quantity == Decimal("0.5")
    assert price.price_eur == Decimal("98765.43")
    assert lot.coin == "BTC"
    assert sale.coin == "ETH"
    assert all(
        value.utcoffset() == timedelta(0)
        for value in (
            session.started_at,
            lot.occurred_at,
            price.captured_at,
            audit.occurred_at,
            raw.created_at,
        )
    )


def test_import_session_accepts_valid_end_time() -> None:
    session = ImportSession(
        source="file",
        version="1",
        status=ImportStatus.COMPLETED,
        started_at=NOW,
        correlation_id=new_id(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
        ended_at=NOW + timedelta(minutes=1),
    )
    assert session.ended_at == NOW + timedelta(minutes=1)


def test_import_session_rejects_end_before_start() -> None:
    with pytest.raises(ValueError, match="earlier"):
        ImportSession(
            source="file",
            version="1",
            status=ImportStatus.FAILED,
            started_at=NOW,
            correlation_id=new_id(),
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
            ended_at=NOW - timedelta(seconds=1),
        )


@pytest.mark.parametrize(
    "factory",
    [
        lambda: EarnLot(
            lot_id=new_id(),
            coin="BTC",
            quantity=Decimal("0"),
            occurred_at=NOW,
            import_session_id=new_id(),
        ),
        lambda: Sale(
            coin="BTC",
            quantity=Decimal("-1"),
            occurred_at=NOW,
            import_session_id=new_id(),
        ),
        lambda: PriceSnapshot(
            coin="BTC", captured_at=NOW, price_eur=Decimal("0"), source="file"
        ),
    ],
)
def test_amounts_must_be_positive(factory: object) -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        factory()  # type: ignore[operator]


def test_amounts_must_be_decimal() -> None:
    with pytest.raises(TypeError, match="Decimal"):
        positive_decimal("1", "quantity")  # type: ignore[arg-type]


def test_text_and_time_validation() -> None:
    with pytest.raises(ValueError, match="empty"):
        required_text(" ", "source")
    with pytest.raises(ValueError, match="timezone-aware"):
        require_utc(datetime(2026, 1, 1))
    assert utc_now().utcoffset() == timedelta(0)


def test_identifier_generators_return_unique_uuids() -> None:
    generator = Uuid4IdGenerator()
    assert generator.new() != generator.new()
    assert new_id().version == 4
