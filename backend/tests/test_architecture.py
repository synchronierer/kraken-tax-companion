from decimal import Decimal

import pytest
from sqlalchemy import Numeric
from sqlalchemy.dialects import postgresql, sqlite

from app.api.dependencies import build_container
from app.core.identifiers import Uuid4IdGenerator
from app.database.types import ExactDecimal, UtcDateTime
from app.services.domain import ServiceDependencies


def test_dependency_container_supplies_explicit_dependencies() -> None:
    container = build_container()
    assert container.settings.environment == "development"
    assert isinstance(container.id_generator, Uuid4IdGenerator)


def test_service_dependencies_hold_future_transaction_factory() -> None:
    def factory() -> None:
        return None

    dependencies = ServiceDependencies(
        unit_of_work_factory=factory,  # type: ignore[arg-type]
        id_generator=Uuid4IdGenerator(),
    )
    assert dependencies.unit_of_work_factory is factory


def test_database_types_cover_null_and_postgresql_paths() -> None:
    utc_type = UtcDateTime()
    decimal_type = ExactDecimal()
    sqlite_dialect = sqlite.dialect()
    postgres_dialect = postgresql.dialect()

    assert utc_type.process_result_value(None, sqlite_dialect) is None
    assert decimal_type.load_dialect_impl(sqlite_dialect).__class__.__name__ == "String"
    assert isinstance(decimal_type.load_dialect_impl(postgres_dialect), Numeric)
    assert decimal_type.process_bind_param(None, sqlite_dialect) is None
    assert decimal_type.process_bind_param(
        Decimal("1.25"), postgres_dialect
    ) == Decimal("1.25")
    assert decimal_type.process_result_value(None, sqlite_dialect) is None
    assert decimal_type.process_result_value("1.25", sqlite_dialect) == Decimal("1.25")
    with pytest.raises(TypeError, match="Decimal"):
        decimal_type.process_bind_param("1.25", sqlite_dialect)  # type: ignore[arg-type]
