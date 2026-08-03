from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.adapters.kraken.assets import (
    ASSET_MAPPING_VERSION,
    LEGACY_ASSET_MAPPING_VERSION,
    normalize_kraken_asset,
    resolve_asset,
    resolve_asset_legacy_v1,
    resolve_pair,
)
from app.adapters.kraken.transformation import (
    KrakenTransformationService,
    TransformationProblemKind,
)
from app.core.entities import (
    AuditActorType,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.identifiers import new_id
from app.core.tax import InventoryLot, TaxCalculationRun
from app.core.transformation import (
    AcquisitionLot,
    DecisionType,
    DisposalEvent,
    DomainProvenance,
    FeeEvent,
    MappingStatus,
    ReconciliationStatus,
    TradeExecution,
    TransformationDecision,
    TransformationRun,
    TransformationStatus,
    ValuationRequirement,
    non_negative_decimal,
)
from app.core.valuation import ValuationDecision
from app.database.base import Base
from app.database.mappings import transformation_decisions
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.hashing import canonical_sha256
from app.transformations.state_machine import transition_transformation

NOW = datetime(2026, 3, 6, 12, tzinfo=UTC)


def database_factory() -> sessionmaker[Session]:
    models.configure_mappings()
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def store_records(
    factory: sessionmaker[Session],
    records: list[tuple[str, str, dict[str, str]]],
) -> UUID:
    imported = ImportSession(
        source=records[0][0],
        version="synthetic-v1",
        status=ImportStatus.COMPLETED,
        started_at=NOW,
        ended_at=NOW,
        correlation_id=new_id(),
        actor_type=AuditActorType.SYSTEM,
        actor_id="test-suite",
    )
    with factory() as database:
        database.add(imported)
        for sequence, (source, external_id, payload) in enumerate(records):
            database.add(
                RawImportRecord(
                    import_session_id=imported.id,
                    source=source,
                    content_hash=canonical_sha256(payload),
                    payload=payload,
                    sequence_number=sequence,
                    external_id=external_id,
                )
            )
        database.commit()
    return imported.id


def transform(
    factory: sessionmaker[Session],
    *session_ids: UUID,
    version: str = "kraken-domain-v1",
):
    return KrakenTransformationService(
        unit_of_work_factory=lambda: SqlAlchemyUnitOfWork(factory),
        clock=lambda: NOW,
    ).transform(
        import_session_ids=session_ids,
        actor_id="test-suite",
        contract_version=version,
    )


def ledger(
    txid: str,
    kind: str,
    amount: str,
    *,
    asset: str = "XXBT",
    fee: str = "0",
    subtype: str = "",
    refid: str = "",
) -> tuple[str, str, dict[str, str]]:
    return (
        "kraken-ledgers",
        f"kraken:ledger:{txid}",
        {
            "txid": txid,
            "time": "2026-03-06 12:00:00",
            "type": kind,
            "subtype": subtype,
            "asset": asset,
            "amount": amount,
            "fee": fee,
            "refid": refid,
        },
    )


def trade(
    txid: str,
    pair: str,
    side: str,
    *,
    price: str = "100",
    cost: str = "200",
    volume: str = "2",
    fee: str = "0.2",
    ledgers: str = "",
    order: str = "O1",
) -> tuple[str, str, dict[str, str]]:
    return (
        "kraken-trades",
        f"kraken:trade:{txid}",
        {
            "txid": txid,
            "ordertxid": order,
            "pair": pair,
            "time": "2026-03-06 12:00:00",
            "type": side,
            "ordertype": "limit",
            "price": price,
            "cost": cost,
            "fee": fee,
            "vol": volume,
            "ledgers": ledgers,
        },
    )


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("BTC", "BTC"),
        ("XBT", "BTC"),
        ("XXBT", "BTC"),
        ("ETH", "ETH"),
        ("XETH", "ETH"),
        ("EUR", "EUR"),
        ("ZEUR", "EUR"),
        ("USD", "USD"),
        ("ZUSD", "USD"),
    ],
)
def test_asset_registry_is_explicit_and_versioned(raw: str, canonical: str) -> None:
    asset = resolve_asset(raw)
    assert asset.raw_code == raw
    assert asset.canonical_code == canonical
    assert asset.mapping_version == ASSET_MAPPING_VERSION
    assert asset.mapping_status is MappingStatus.MAPPED
    assert asset.review_reason is None


def test_new_asset_is_identity_mapped_and_legacy_v1_remains_auditable() -> None:
    asset = resolve_asset("XUNKNOWN")
    assert asset.raw_code == "XUNKNOWN"
    assert asset.canonical_code == "XUNKNOWN"
    assert asset.mapping_status is MappingStatus.MAPPED
    assert asset.review_reason is None
    legacy = resolve_asset_legacy_v1("XUNKNOWN")
    assert legacy.canonical_code is None
    assert legacy.mapping_version == LEGACY_ASSET_MAPPING_VERSION
    assert legacy.mapping_status is MappingStatus.UNRESOLVED
    assert legacy.review_reason == "asset_alias_unknown"
    assert resolve_asset_legacy_v1("ZGBP").canonical_code is None
    assert resolve_asset("ZGBP").canonical_code == "GBP"
    assert normalize_kraken_asset("XUNKNOWN").normalized_asset == "XUNKNOWN"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BTC/EUR", ("BTC", "EUR")),
        ("XXBTZEUR", ("BTC", "EUR")),
        ("XETHXXBT", ("ETH", "BTC")),
    ],
)
def test_pair_resolution_is_conservative(raw: str, expected: tuple[str, str]) -> None:
    pair = resolve_pair(raw)
    assert pair is not None
    assert pair.raw_pair == raw
    assert (pair.base.canonical_code, pair.quote.canonical_code) == expected
    assert resolve_pair("BTC/EUR/USD") is None
    assert resolve_pair("BTC/XUNKNOWN") is not None
    assert resolve_pair("BTC/X!") is None
    assert resolve_pair("UNKNOWNPAIR") is None


def test_domain_guards_and_transformation_state_machine() -> None:
    assert non_negative_decimal(Decimal("0"), "fee") == 0
    with pytest.raises(TypeError, match="Decimal"):
        non_negative_decimal("0", "fee")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="negative"):
        non_negative_decimal(Decimal("-1"), "fee")
    run = TransformationRun(
        contract_version="v1",
        status=TransformationStatus.CREATED,
        started_at=NOW,
        actor_id="tester",
    )
    transition_transformation(run, TransformationStatus.PROCESSING, NOW)
    transition_transformation(run, TransformationStatus.COMPLETED, NOW)
    assert run.completed_at == NOW
    with pytest.raises(ValueError, match="not allowed"):
        transition_transformation(run, TransformationStatus.FAILED, NOW)
    with pytest.raises(ValueError, match="checked_records"):
        TransformationRun(
            contract_version="v1",
            status=TransformationStatus.CREATED,
            started_at=NOW,
            actor_id="tester",
            checked_records=-1,
        )
    completed = TransformationRun(
        contract_version="v1",
        status=TransformationStatus.COMPLETED,
        started_at=NOW,
        completed_at=NOW,
        actor_id="tester",
    )
    assert completed.completed_at == NOW


def test_rewards_internal_movements_and_review_cases() -> None:
    factory = database_factory()
    records = [
        ledger("R1", "earn", "1.5", fee="0.1", subtype="reward"),
        ledger("R2", "staking", "2"),
        ledger("R3", "earn", "0", subtype="reward"),
        ledger("R4", "earn", "-1", subtype="reward"),
        ledger("R5", "earn", "1", subtype="future"),
        ledger("R6", "staking", "1", subtype="future"),
        ledger("R7", "earn", "1", asset="XUNKNOWN", subtype="reward"),
        ledger("R8", "earn", "1", fee="2", subtype="reward"),
        ledger("U1", "future", "1"),
    ]
    records.extend(
        ledger(f"I{index}", "earn", "1", subtype=subtype)
        for index, subtype in enumerate(
            (
                "allocation",
                "autoallocate",
                "deallocation",
                "migration",
                "spottostaking",
                "stakingfromspot",
                "stakingtospot",
                "spotfromstaking",
            )
        )
    )
    session_id = store_records(factory, records)
    result = transform(factory, session_id)
    assert result.status is TransformationStatus.COMPLETED_WITH_REVIEW
    assert result.rewards == 2
    assert result.acquisitions == 2
    assert result.internal_movements == 8
    assert result.review_cases == 6
    assert result.valuation_requirements == 2
    with factory() as database:
        lots = tuple(database.scalars(select(AcquisitionLot)))
        assert {lot.quantity for lot in lots} == {Decimal("1.4"), Decimal("2")}
        first = next(lot for lot in lots if lot.gross_quantity == Decimal("1.5"))
        assert first.fee_quantity == Decimal("0.1")
        assert first.asset_raw_code == "XXBT"
        assert first.asset_code == "BTC"
        assert first.occurred_at.tzinfo is UTC
        decisions = tuple(database.scalars(select(TransformationDecision)))
        assert len(decisions) == len(records)
        assert (
            sum(
                decision.decision_type is DecisionType.INTERNAL_MOVEMENT
                for decision in decisions
            )
            == 8
        )


def test_trade_projection_eur_crypto_fees_and_reconciliation() -> None:
    factory = database_factory()
    ledger_session = store_records(
        factory,
        [
            ledger("L1", "trade", "-200", asset="ZEUR"),
            ledger("L2", "trade", "-2", asset="XXBT"),
        ],
    )
    trade_session = store_records(
        factory,
        [
            trade("T1", "XXBTZEUR", "buy", ledgers="L1"),
            trade("T2", "XXBTZEUR", "sell", order="O1", ledgers="ABSENT"),
            trade("T3", "XETHXXBT", "buy", fee="0.01", order="O1"),
            trade("T4", "XETHXXBT", "sell", fee="0", ledgers="L2,MISSING"),
        ],
    )
    result = transform(factory, ledger_session, trade_session)
    assert result.trade_executions == 4
    assert result.acquisitions == 4
    assert result.disposals == 3
    with factory() as database:
        executions = tuple(database.scalars(select(TradeExecution)))
        assert [item.order_external_id for item in executions].count("O1") == 4
        assert {item.external_id for item in executions} == {
            "kraken:trade:T1",
            "kraken:trade:T2",
            "kraken:trade:T3",
            "kraken:trade:T4",
        }
        assert (
            next(
                item for item in executions if item.external_id.endswith("T1")
            ).reconciliation_status
            is ReconciliationStatus.MATCHED
        )
        assert (
            next(
                item for item in executions if item.external_id.endswith("T4")
            ).reconciliation_status
            is ReconciliationStatus.PARTIAL
        )
        assert (
            next(
                item for item in executions if item.external_id.endswith("T2")
            ).reconciliation_status
            is ReconciliationStatus.PENDING
        )
        assert database.scalar(select(func.count()).select_from(FeeEvent)) == 3
        assert database.scalar(select(func.count()).select_from(DisposalEvent)) == 3
        assert (
            database.scalar(select(func.count()).select_from(ValuationRequirement))
            == result.valuation_requirements
        )


@pytest.mark.parametrize(
    ("record", "code"),
    [
        (trade("B1", "UNKNOWN", "buy"), "trade_pair_unresolved"),
        (trade("B2", "XXBTZEUR", "future"), "trade_side_unsupported"),
        (
            trade("B3", "XXBTZEUR", "buy", price="0"),
            "trade_amount_invalid",
        ),
        (
            trade("B4", "XXBTZEUR", "buy", cost="201"),
            "trade_cost_mismatch",
        ),
    ],
)
def test_invalid_trades_become_structured_review(
    record: tuple[str, str, dict[str, str]], code: str
) -> None:
    factory = database_factory()
    result = transform(factory, store_records(factory, [record]))
    assert result.status is TransformationStatus.COMPLETED_WITH_REVIEW
    assert result.problems[0].code == code
    assert result.problems[0].kind is TransformationProblemKind.REVIEW
    assert result.trade_executions == 0


def test_ledger_only_grouping_and_missing_reference() -> None:
    factory = database_factory()
    session_id = store_records(
        factory,
        [
            ledger("S1", "spend", "-100", asset="ZEUR", refid="REF1"),
            ledger("R1", "receive", "1", refid="REF1"),
            ledger("S2", "spend", "-50", asset="ZEUR"),
        ],
    )
    result = transform(factory, session_id)
    assert result.acquisitions == 1
    assert result.review_cases == 1
    with factory() as database:
        provenance = tuple(database.scalars(select(DomainProvenance)))
        assert len(provenance) == 2
        assert len({item.domain_object_id for item in provenance}) == 1
        assert (
            database.scalar(select(func.count()).select_from(TransformationDecision))
            == 3
        )


def test_trade_ledger_asset_conflict_is_not_projected() -> None:
    factory = database_factory()
    ledger_session = store_records(factory, [ledger("L1", "trade", "1", asset="XETH")])
    trade_session = store_records(
        factory, [trade("T1", "XXBTZEUR", "buy", ledgers="L1")]
    )
    result = transform(factory, ledger_session, trade_session)
    assert result.conflicts == 1
    assert result.trade_executions == 0


def test_ambiguous_ledger_group_is_conflict() -> None:
    factory = database_factory()
    session_id = store_records(
        factory,
        [
            ledger("S1", "spend", "-100", refid="REF"),
            ledger("S2", "spend", "-50", refid="REF"),
            ledger("R1", "receive", "1", refid="REF"),
        ],
    )
    result = transform(factory, session_id)
    assert result.conflicts == 3
    assert all(
        problem.kind is TransformationProblemKind.CONFLICT
        for problem in result.problems
    )


def test_projection_idempotency_conflict_and_new_version() -> None:
    factory = database_factory()
    first_session = store_records(
        factory, [ledger("R1", "earn", "1", subtype="reward")]
    )
    first = transform(factory, first_session)
    duplicate_session = store_records(
        factory, [ledger("R1", "earn", "1", subtype="reward")]
    )
    duplicate = transform(factory, duplicate_session)
    conflict_session = store_records(
        factory, [ledger("R1", "earn", "2", subtype="reward")]
    )
    conflict = transform(factory, conflict_session)
    reprocessed = transform(factory, duplicate_session, version="kraken-domain-v2")
    assert first.rewards == 1
    assert duplicate.rewards == 0
    assert conflict.conflicts == 1
    assert reprocessed.rewards == 0
    assert reprocessed.reused_objects == 1
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 1
        duplicate_decision = database.scalar(
            select(TransformationDecision).where(
                transformation_decisions.c.transformation_run_id == duplicate.run_id
            )
        )
        assert duplicate_decision is not None
        assert duplicate_decision.decision_type is DecisionType.DOMAIN_EVENT_REUSED


def test_v2_reuses_v1_rewards_and_maps_the_real_asset_matrix() -> None:
    factory = database_factory()
    current_assets = (
        "KAVA21.S",
        "GRT28.S",
        "XTZ.S",
        "DOT28.S",
        "ATOM21.S",
        "ADA.S",
        "XXBT.B",
        "KAVA",
        "GRT",
        "XETH.B",
        "EIGEN",
        "XTZ.B",
        "DOT",
    )
    records = [
        ledger(f"KNOWN-{index}", "earn", "1", asset="XETH", subtype="reward")
        for index in range(4)
    ]
    records.extend(
        ledger(
            f"NEW-{index}",
            "earn",
            "1",
            asset=current_assets[index % len(current_assets)],
            fee="0.1" if index == 0 else "0",
            subtype="reward",
        )
        for index in range(51)
    )
    session_id = store_records(factory, records)

    legacy = transform(factory, session_id, version="kraken-domain-v1")
    assert legacy.checked_records == 55
    assert legacy.acquisitions == 4
    assert legacy.review_cases == 51
    with factory() as database:
        legacy_decisions = tuple(
            database.scalars(
                select(TransformationDecision).where(
                    transformation_decisions.c.transformation_run_id == legacy.run_id
                )
            )
        )
        assert (
            sum(
                decision.reason_code == "asset_alias_unknown"
                for decision in legacy_decisions
            )
            == 51
        )

    current = transform(factory, session_id, version="kraken-domain-v2")
    assert current.status is TransformationStatus.COMPLETED
    assert current.checked_records == 55
    assert current.acquisitions == 51
    assert current.reused_objects == 4
    assert current.review_cases == current.conflicts == 0
    assert current.valuation_requirements == 51

    repeated = transform(factory, session_id, version="kraken-domain-v2")
    assert repeated.status is TransformationStatus.COMPLETED
    assert repeated.checked_records == 55
    assert repeated.acquisitions == 0
    assert repeated.reused_objects == 55
    assert repeated.review_cases == repeated.conflicts == 0
    assert repeated.valuation_requirements == 0

    with factory() as database:
        acquisitions = tuple(database.scalars(select(AcquisitionLot)))
        assert len(acquisitions) == 55
        assert database.scalar(select(func.count()).select_from(ValuationDecision)) == 0
        assert database.scalar(select(func.count()).select_from(InventoryLot)) == 0
        assert database.scalar(select(func.count()).select_from(TaxCalculationRun)) == 0
        assert {item.asset_code for item in acquisitions}.issuperset(
            {"ADA", "ATOM", "BTC", "DOT", "EIGEN", "ETH", "GRT", "KAVA", "XTZ"}
        )
        fee_reward = next(
            item for item in acquisitions if item.external_id.endswith("NEW-0")
        )
        assert fee_reward.gross_quantity == Decimal("1")
        assert fee_reward.fee_quantity == Decimal("0.1")
        assert fee_reward.quantity == Decimal("0.9")
        assert database.scalar(select(func.count()).select_from(FeeEvent)) == 0
        current_decisions = tuple(
            database.scalars(
                select(TransformationDecision).where(
                    transformation_decisions.c.transformation_run_id == current.run_id
                )
            )
        )
        assert len(current_decisions) == 55
        assert (
            sum(
                decision.decision_type is DecisionType.DOMAIN_EVENT_REUSED
                for decision in current_decisions
            )
            == 4
        )
        assert all(
            decision.contract_version == "kraken-domain-v2"
            for decision in current_decisions
        )
        assert not any(
            decision.reason_code == "asset_alias_unknown"
            for decision in current_decisions
        )
        persisted_legacy_decisions = tuple(
            database.scalars(
                select(TransformationDecision).where(
                    transformation_decisions.c.transformation_run_id == legacy.run_id
                )
            )
        )
        assert persisted_legacy_decisions == legacy_decisions


def test_v2_rejects_only_a_genuinely_invalid_reward_asset() -> None:
    factory = database_factory()
    session_id = store_records(
        factory,
        [ledger("INVALID", "earn", "1", asset="ADA/../../", subtype="reward")],
    )

    result = transform(factory, session_id, version="kraken-domain-v2")

    assert result.status is TransformationStatus.COMPLETED_WITH_REVIEW
    assert result.checked_records == result.review_cases == 1
    assert result.acquisitions == result.valuation_requirements == 0
    assert result.problems[0].code == "asset_alias_unknown"


def test_trade_projection_idempotency_and_conflict() -> None:
    factory = database_factory()
    first_session = store_records(factory, [trade("T1", "XXBTZEUR", "buy")])
    assert transform(factory, first_session).trade_executions == 1
    duplicate_session = store_records(factory, [trade("T1", "XXBTZEUR", "buy")])
    duplicate = transform(factory, duplicate_session)
    conflict_session = store_records(
        factory, [trade("T1", "XXBTZEUR", "buy", fee="0.3")]
    )
    conflict = transform(factory, conflict_session)
    assert duplicate.trade_executions == 0
    assert conflict.conflicts == 1


def test_unsupported_source_and_unknown_group_asset_still_get_decisions() -> None:
    factory = database_factory()
    generic_session = store_records(
        factory,
        [
            (
                "synthetic-source",
                "synthetic:1",
                {"time": "2026-03-06 12:00:00"},
            )
        ],
    )
    grouped_session = store_records(
        factory,
        [
            ledger("S1", "spend", "-1", asset="ZEUR", refid="REF"),
            ledger("R1", "receive", "1", asset="XUNKNOWN", refid="REF"),
        ],
    )
    result = transform(factory, generic_session, grouped_session)
    assert result.checked_records == 3
    assert result.review_cases == 1


class CommitFailingUnitOfWork(SqlAlchemyUnitOfWork):
    def commit(self) -> None:
        raise RuntimeError("synthetic commit failure")


class FailingUnitOfWork(SqlAlchemyUnitOfWork):
    def flush(self) -> None:
        raise RuntimeError("synthetic repository failure")


def test_infrastructure_failure_rolls_back_and_records_failed_run() -> None:
    factory = database_factory()
    session_id = store_records(factory, [ledger("R1", "earn", "1", subtype="reward")])
    calls = 0

    def uow_factory() -> SqlAlchemyUnitOfWork:
        nonlocal calls
        calls += 1
        return (
            FailingUnitOfWork(factory) if calls == 1 else SqlAlchemyUnitOfWork(factory)
        )

    result = KrakenTransformationService(
        unit_of_work_factory=uow_factory, clock=lambda: NOW
    ).transform(import_session_ids=[session_id], actor_id="test-suite")
    assert result.status is TransformationStatus.FAILED
    assert result.problems[0].kind is TransformationProblemKind.INFRASTRUCTURE
    with factory() as database:
        assert database.scalar(select(func.count()).select_from(AcquisitionLot)) == 0
        run = database.get(TransformationRun, result.run_id)
        assert run is not None and run.status is TransformationStatus.FAILED


def test_commit_failure_after_completion_is_recovered_as_failed() -> None:
    factory = database_factory()
    session_id = store_records(factory, [ledger("R1", "earn", "1", subtype="reward")])
    calls = 0

    def uow_factory() -> SqlAlchemyUnitOfWork:
        nonlocal calls
        calls += 1
        return (
            CommitFailingUnitOfWork(factory)
            if calls == 1
            else SqlAlchemyUnitOfWork(factory)
        )

    result = KrakenTransformationService(
        unit_of_work_factory=uow_factory, clock=lambda: NOW
    ).transform(import_session_ids=[session_id], actor_id="test-suite")
    assert result.status is TransformationStatus.FAILED
    with factory() as database:
        run = database.get(TransformationRun, result.run_id)
        assert run is not None and run.status is TransformationStatus.FAILED


def test_failure_evidence_storage_can_also_be_unavailable() -> None:
    factory = database_factory()
    session_id = store_records(factory, [ledger("R1", "earn", "1", subtype="reward")])
    calls = 0

    def uow_factory() -> SqlAlchemyUnitOfWork:
        nonlocal calls
        calls += 1
        if calls == 1:
            return FailingUnitOfWork(factory)
        raise RuntimeError("recovery unavailable")

    result = KrakenTransformationService(
        unit_of_work_factory=uow_factory, clock=lambda: NOW
    ).transform(import_session_ids=[session_id], actor_id="test-suite")
    assert result.status is TransformationStatus.FAILED
