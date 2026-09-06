from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    ImportSession,
    ImportStatus,
    RawImportRecord,
)
from app.core.financial_review import (
    FinancialReviewRecordLink,
    FinancialReviewResolution,
    FinancialReviewSuggestion,
    FinancialReviewType,
    FinancialSuggestionType,
    ResolutionStatus,
    ReviewConfidence,
    SuggestionStatus,
    TaxMappingStatus,
)
from app.core.transformation import (
    DisposalEvent,
    TradeExecution,
    TransformationIssue,
    TransformationRun,
    TransformationStatus,
)
from app.database.base import Base
from app.database.session import get_session
from app.main import app
from app.services.financial_reviews import (
    delisting_suggestions,
    fiat_withdrawal_suggestion,
    ledger_asset,
    ledger_decimal,
    ledger_time,
    resolution_metadata,
)

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
EUR_ID = UUID("74a0167c14aa426c93af2c67e550f9ab")
ETHW_ID = UUID("e9f6f7f4b2bb44f6a3cdd62b8b68e64d")
USDC_ID = UUID("044b0957eedb450ba3ea192354548d36")


def ledger_record(
    session_id: UUID,
    record_id: UUID,
    txid: str,
    timestamp: str,
    kind: str,
    asset: str,
    amount: str,
    fee: str,
    refid: str,
    sequence_number: int = 0,
) -> RawImportRecord:
    return RawImportRecord(
        id=record_id,
        import_session_id=session_id,
        source="kraken-ledgers",
        content_hash=record_id.hex * 2,
        sequence_number=sequence_number,
        external_id=f"kraken:ledger:{txid}",
        payload={
            "txid": txid,
            "time": timestamp,
            "type": kind,
            "asset": asset,
            "amount": amount,
            "fee": fee,
            "refid": refid,
        },
    )


def raw_record(
    session_id: UUID,
    *,
    source: str,
    payload: dict[str, str],
    sequence_number: int = 0,
) -> RawImportRecord:
    record_id = uuid4()
    return RawImportRecord(
        id=record_id,
        import_session_id=session_id,
        source=source,
        content_hash=record_id.hex * 2,
        sequence_number=sequence_number,
        external_id=f"test:{record_id}",
        payload=payload,
    )


@pytest.fixture
def review_database() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as database:
        imported = ImportSession(
            source="kraken-ledgers",
            version="synthetic",
            status=ImportStatus.COMPLETED,
            started_at=NOW,
            ended_at=NOW,
            correlation_id=uuid4(),
            actor_type=AuditActorType.SYSTEM,
            actor_id="test-suite",
        )
        run = TransformationRun(
            contract_version="kraken-domain-v2",
            status=TransformationStatus.COMPLETED_WITH_REVIEW,
            started_at=NOW,
            completed_at=NOW,
            actor_id="test-suite",
            checked_records=3,
            review_cases=3,
        )
        records = (
            ledger_record(
                imported.id,
                EUR_ID,
                "LM5SEI-7DNAK-PDYW6R",
                "2026-09-03 15:06:25.252652",
                "withdrawal",
                "ZEUR",
                "-1999.0000",
                "1.0000",
                "FTBNc8t-BUdvJqOx0vh0z6UWBfXtSk",
                sequence_number=0,
            ),
            ledger_record(
                imported.id,
                ETHW_ID,
                "LBPUC6-BSFAE-YOBIDI",
                "2026-03-05 20:43:26.683375",
                "transfer",
                "ETHW",
                "-2.0000057",
                "0",
                "LAUUY4P-PMRFB-CCRQHC",
                sequence_number=1,
            ),
            ledger_record(
                imported.id,
                USDC_ID,
                "L5S774-ECKWH-RWRIV5",
                "2026-03-09 21:00:30.393365",
                "transfer",
                "USDC",
                "+0.60700172",
                "0",
                "LAL7WE7-5XLGW-FCIW3C",
                sequence_number=2,
            ),
        )
        database.add_all((imported, run, *records))
        database.flush()
        for record in records:
            database.add(
                TransformationIssue(
                    transformation_run_id=run.id,
                    raw_import_record_id=record.id,
                    code=f"ledger_{record.payload['type']}_requires_review",
                    message="Financial record requires review.",
                    is_conflict=False,
                    occurred_at=NOW,
                )
            )
        database.commit()
        yield database
    engine.dispose()


@pytest.fixture
def review_client(review_database: Session) -> Iterator[TestClient]:
    def dependency() -> Iterator[Session]:
        yield review_database

    app.dependency_overrides[get_session] = dependency
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()


def issue_id(database: Session, raw_id: UUID) -> str:
    issue = database.scalar(
        select(TransformationIssue).where(
            TransformationIssue.raw_import_record_id == raw_id
        )
    )
    assert issue is not None
    return str(issue.id)


def decision_payload(
    status: str, resolution_type: str, record_ids: list[UUID]
) -> dict[str, object]:
    return {
        "status": status,
        "resolution_type": resolution_type,
        "record_ids": [str(value) for value in record_ids],
        "decided_by": "local-user",
        "reason": "Vom Kontoinhaber anhand der Originalbelege bestätigt.",
    }


def test_irrelevant_non_kraken_record_without_asset_is_skipped(
    review_database: Session,
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    assert session_id is not None
    record = raw_record(
        session_id,
        source="other-provider",
        payload={"type": "withdrawal"},
    )

    assert fiat_withdrawal_suggestion(record, has_trade_context=False) is None


def test_non_withdrawal_kraken_record_without_asset_is_skipped(
    review_database: Session,
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    assert session_id is not None
    record = raw_record(
        session_id,
        source="kraken-ledgers",
        payload={"type": "deposit"},
    )

    assert fiat_withdrawal_suggestion(record, has_trade_context=False) is None


def test_malformed_kraken_withdrawal_without_asset_is_skipped(
    review_database: Session,
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    assert session_id is not None
    record = raw_record(
        session_id,
        source="kraken-ledgers",
        payload={"type": "withdrawal", "amount": "-10", "fee": "1"},
    )

    assert fiat_withdrawal_suggestion(record, has_trade_context=False) is None
    with pytest.raises(ValueError, match="Kraken ledger asset must not be empty"):
        ledger_asset(record)


def test_malformed_kraken_transfer_without_valid_asset_is_skipped(
    review_database: Session,
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    assert session_id is not None
    missing_asset = raw_record(
        session_id,
        source="kraken-ledgers",
        payload={
            "type": "transfer",
            "time": "2026-03-06 00:00:00",
            "amount": "-1",
            "fee": "0",
        },
    )
    invalid_asset = raw_record(
        session_id,
        source="kraken-ledgers",
        payload={
            "type": "transfer",
            "time": "2026-03-06 00:00:00",
            "asset": "??",
            "amount": "-1",
            "fee": "0",
        },
    )
    valid_incoming = ledger_record(
        session_id,
        uuid4(),
        "valid-incoming",
        "2026-03-07 00:00:00",
        "transfer",
        "USDC",
        "+1",
        "0",
        "valid-incoming",
    )

    assert (
        delisting_suggestions(
            [missing_asset, invalid_asset, valid_incoming], excluded_record_ids=set()
        )
        == []
    )
    with pytest.raises(ValueError, match="Invalid Kraken ledger asset"):
        ledger_asset(invalid_asset)


def test_suggestion_endpoint_skips_irrelevant_and_malformed_records(
    review_client: TestClient, review_database: Session
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    transformation_run_id = review_database.scalar(select(TransformationRun.id))
    assert session_id is not None
    assert transformation_run_id is not None
    mixed_records = [
        raw_record(
            session_id,
            source="other-provider",
            payload={"type": "withdrawal"},
            sequence_number=3,
        ),
        raw_record(
            session_id,
            source="kraken-ledgers",
            payload={"type": "deposit"},
            sequence_number=4,
        ),
        raw_record(
            session_id,
            source="kraken-ledgers",
            payload={"type": "withdrawal", "amount": "-10", "fee": "1"},
            sequence_number=5,
        ),
        raw_record(
            session_id,
            source="kraken-ledgers",
            payload={
                "type": "transfer",
                "time": "2026-03-06 00:00:00",
                "asset": "",
                "amount": "-1",
                "fee": "0",
            },
            sequence_number=6,
        ),
    ]
    review_database.add_all(mixed_records)
    review_database.flush()
    review_database.add_all(
        TransformationIssue(
            transformation_run_id=transformation_run_id,
            raw_import_record_id=record.id,
            code="malformed_financial_review",
            message="Malformed or irrelevant test record.",
            is_conflict=False,
            occurred_at=NOW,
        )
        for record in mixed_records
    )
    review_database.commit()

    response = review_client.post("/api/financial-review-suggestions")

    assert response.status_code == 200
    assert response.json()["created_count"] == 2
    suggestions = list(review_database.scalars(select(FinancialReviewSuggestion)))
    assert {item.suggestion_type for item in suggestions} == {
        FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL,
        FinancialSuggestionType.POSSIBLE_DELISTING_LIQUIDATION,
    }


def test_current_suggestions_are_explainable_and_not_resolutions(
    review_client: TestClient, review_database: Session
) -> None:
    response = review_client.post("/api/financial-review-suggestions")
    assert response.status_code == 200
    assert response.json()["created_count"] == 2
    assert (
        review_client.post("/api/financial-review-suggestions").json()["created_count"]
        == 0
    )
    suggestions = list(review_database.scalars(select(FinancialReviewSuggestion)))
    assert len(suggestions) == 2
    assert (
        review_database.scalar(
            select(func.count()).select_from(FinancialReviewResolution)
        )
        == 0
    )
    withdrawal = next(
        item
        for item in suggestions
        if item.suggestion_type.value == "own_account_fiat_withdrawal"
    )
    assert withdrawal.status is SuggestionStatus.SUGGESTED
    assert withdrawal.confidence is ReviewConfidence.HIGH
    assert withdrawal.metadata == {
        "asset": "EUR",
        "kraken_ledger_amount": "-1999.0000",
        "withdrawal_fee": "1.0000",
        "gross_kraken_debit": "2000.0000",
        "destination_relation": "UNCONFIRMED",
    }
    listing = review_client.get("/api/financial-reviews").json()
    assert listing["total"] == 3
    pair_rows = [
        item
        for item in listing["items"]
        if item["raw_record"]["id"] in {str(ETHW_ID), str(USDC_ID)}
    ]
    assert len(pair_rows) == 2
    assert all(len(item["suggestion"]["records"]) == 2 for item in pair_rows)
    assert {record["refid"] for record in pair_rows[0]["suggestion"]["records"]} == {
        "LAUUY4P-PMRFB-CCRQHC",
        "LAL7WE7-5XLGW-FCIW3C",
    }


def test_confirm_withdrawal_is_idempotent_and_has_no_crypto_disposal(
    review_client: TestClient, review_database: Session
) -> None:
    review_client.post("/api/financial-review-suggestions")
    url = f"/api/reviews/{issue_id(review_database, EUR_ID)}/resolve"
    payload = decision_payload("confirmed", "own_account_fiat_withdrawal", [EUR_ID])
    first = review_client.post(url, json=payload)
    assert first.status_code == 200
    result = first.json()["resolution"]
    assert result["tax_mapping_status"] == "not_required"
    assert result["metadata"] == {
        "asset": "EUR",
        "kraken_ledger_amount": "-1999.0000",
        "withdrawal_fee": "1.0000",
        "gross_kraken_debit": "2000.0000",
        "net_external_credit": "1999.0000",
        "destination_relation": "OWN_ACCOUNT",
        "tax_mapping": "NO_CRYPTO_DISPOSAL",
        "fee_type": "WITHDRAWAL_FEE",
        "fee_tax_status": "REVIEW_REQUIRED",
        "confirmation_source": "USER_CONFIRMED",
    }
    duplicate = review_client.post(url, json=payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["duplicate"] is True
    assert (
        review_database.scalar(
            select(func.count()).select_from(FinancialReviewResolution)
        )
        == 1
    )
    assert review_database.scalar(select(func.count()).select_from(DisposalEvent)) == 0
    assert (
        review_client.post("/api/financial-review-suggestions").json()["created_count"]
        == 0
    )
    assert review_client.get("/api/dashboard").json()["review_cases"] == 2
    open_reviews = review_client.get("/api/reviews").json()["items"]
    assert issue_id(review_database, EUR_ID) not in {
        item["id"] for item in open_reviews
    }
    conflict = review_client.post(
        url,
        json=decision_payload("confirmed", "delisting_liquidation", [EUR_ID]),
    )
    assert conflict.status_code == 409


def test_confirm_delisting_keeps_window_refids_and_pending_tax_mapping(
    review_client: TestClient, review_database: Session
) -> None:
    review_client.post("/api/financial-review-suggestions")
    url = f"/api/reviews/{issue_id(review_database, ETHW_ID)}/resolve"
    response = review_client.post(
        url,
        json=decision_payload("confirmed", "delisting_liquidation", [ETHW_ID, USDC_ID]),
    )
    assert response.status_code == 200
    resolution = response.json()["resolution"]
    assert resolution["tax_mapping_status"] == "pending"
    assert resolution["metadata"]["disposed_asset"] == "ETHW"
    assert resolution["metadata"]["disposed_quantity"] == "2.0000057"
    assert resolution["metadata"]["proceeds_asset"] == "USDC"
    assert resolution["metadata"]["proceeds_quantity"] == "0.60700172"
    assert resolution["metadata"]["event_window_start"].startswith(
        "2026-03-05T20:43:26.683375"
    )
    assert resolution["metadata"]["event_window_end"].startswith(
        "2026-03-09T21:00:30.393365"
    )
    assert "execution" not in " ".join(resolution["metadata"]).lower()
    assert review_database.scalar(select(func.count()).select_from(TradeExecution)) == 0
    audits = list(
        review_database.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "financial_review.resolution_confirmed"
            )
        )
    )
    assert audits[0].metadata["provider_refids"] == [
        "LAUUY4P-PMRFB-CCRQHC",
        "LAL7WE7-5XLGW-FCIW3C",
    ]
    listing = review_client.get("/api/financial-reviews").json()["items"]
    linked = [item for item in listing if item["raw_record"]["id"] == str(USDC_ID)]
    assert linked[0]["resolution"]["resolution_type"] == "delisting_liquidation"


def test_rejection_is_persisted_and_idempotent(
    review_client: TestClient, review_database: Session
) -> None:
    review_client.post("/api/financial-review-suggestions")
    url = f"/api/reviews/{issue_id(review_database, EUR_ID)}/resolve"
    payload = decision_payload("rejected", "own_account_fiat_withdrawal", [EUR_ID])
    first = review_client.post(url, json=payload)
    assert first.status_code == 200
    assert first.json()["suggestion"]["status"] == "rejected"
    assert review_client.post(url, json=payload).json()["duplicate"] is True
    assert (
        review_database.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "financial_review.suggestion_rejected")
        )
        == 1
    )
    conflicting_confirmation = review_client.post(
        url,
        json=decision_payload("confirmed", "own_account_fiat_withdrawal", [EUR_ID]),
    )
    assert conflicting_confirmation.status_code == 409
    assert (
        review_database.scalar(
            select(func.count()).select_from(FinancialReviewResolution)
        )
        == 0
    )


def test_api_rejects_non_reviews_bad_records_and_invalid_shapes(
    review_client: TestClient, review_database: Session
) -> None:
    missing = review_client.post(
        f"/api/reviews/{uuid4()}/resolve",
        json=decision_payload("confirmed", "own_account_fiat_withdrawal", [EUR_ID]),
    )
    assert missing.status_code == 404
    issue = issue_id(review_database, EUR_ID)
    duplicate_ids = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload("confirmed", "delisting_liquidation", [EUR_ID, EUR_ID]),
    )
    assert duplicate_ids.status_code == 422
    mismatched_issue_record = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload("confirmed", "own_account_fiat_withdrawal", [uuid4()]),
    )
    assert mismatched_issue_record.status_code == 422
    assert mismatched_issue_record.json()["detail"]["code"] == (
        "financial_review_records_invalid"
    )
    missing_record = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload("confirmed", "delisting_liquidation", [EUR_ID, uuid4()]),
    )
    assert missing_record.status_code == 422
    assert missing_record.json()["detail"]["code"] == (
        "financial_review_records_invalid"
    )
    imported = review_database.scalar(select(ImportSession))
    assert imported is not None
    unreviewed = ledger_record(
        imported.id,
        uuid4(),
        "not-reviewed",
        "2026-09-01 00:00:00",
        "transfer",
        "USDC",
        "+1",
        "0",
        "not-reviewed",
        sequence_number=3,
    )
    review_database.add(unreviewed)
    review_database.commit()
    not_a_review = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload(
            "confirmed", "delisting_liquidation", [EUR_ID, unreviewed.id]
        ),
    )
    assert not_a_review.status_code == 422
    missing_suggestion = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload("rejected", "own_account_fiat_withdrawal", [EUR_ID]),
    )
    assert missing_suggestion.status_code == 422
    invalid_resolution = review_client.post(
        f"/api/reviews/{issue}/resolve",
        json=decision_payload("confirmed", "delisting_liquidation", [EUR_ID]),
    )
    assert invalid_resolution.status_code == 422

    transformation_run_id = review_database.scalar(select(TransformationRun.id))
    assert transformation_run_id is not None
    review_database.add(
        TransformationIssue(
            transformation_run_id=transformation_run_id,
            raw_import_record_id=uuid4(),
            code="orphaned_financial_review",
            message="The referenced raw record is unavailable.",
            is_conflict=False,
            occurred_at=NOW,
        )
    )
    review_database.commit()
    listing = review_client.get("/api/financial-reviews")
    assert listing.status_code == 200
    assert listing.json()["total"] == 3
    assert all(
        item["code"] != "orphaned_financial_review" for item in listing.json()["items"]
    )


def test_conservative_engine_does_not_pair_weak_or_ambiguous_evidence(
    review_database: Session,
) -> None:
    session_id = review_database.scalar(select(ImportSession.id))
    assert session_id is not None
    weak = ledger_record(
        session_id,
        uuid4(),
        "weak",
        "2026-04-20 00:00:00",
        "transfer",
        "BTC",
        "+1",
        "0",
        "weak",
    )
    charged = ledger_record(
        session_id,
        uuid4(),
        "charged",
        "2026-03-06 00:00:00",
        "transfer",
        "USDC",
        "+1",
        "1",
        "charged",
    )
    malformed = ledger_record(
        session_id,
        uuid4(),
        "bad",
        "not-a-time",
        "transfer",
        "USDC",
        "bad",
        "0",
        "bad",
    )
    records = list(review_database.scalars(select(RawImportRecord)))
    assert (
        delisting_suggestions(
            records + [weak, charged, malformed], excluded_record_ids={ETHW_ID}
        )
        == []
    )
    second_incoming = ledger_record(
        session_id,
        uuid4(),
        "ambiguous",
        "2026-03-08 00:00:00",
        "transfer",
        "USDT",
        "+1",
        "0",
        "ambiguous",
    )
    assert (
        delisting_suggestions(records + [second_incoming], excluded_record_ids=set())
        == []
    )
    assert fiat_withdrawal_suggestion(records[0], has_trade_context=True) is None
    assert fiat_withdrawal_suggestion(weak, has_trade_context=False) is None
    aware = ledger_record(
        session_id,
        uuid4(),
        "aware",
        "2026-03-09T22:00:00+01:00",
        "transfer",
        "USDC",
        "+1",
        "0",
        "aware",
    )
    assert ledger_time(aware).tzinfo is UTC
    invalid_asset = ledger_record(
        session_id,
        uuid4(),
        "invalid-asset",
        "2026-03-09 00:00:00",
        "transfer",
        "??",
        "+1",
        "0",
        "invalid-asset",
    )
    with pytest.raises(ValueError, match="Invalid Kraken ledger asset"):
        ledger_asset(invalid_asset)
    with pytest.raises(ValueError, match="Invalid Kraken ledger amount"):
        ledger_decimal(malformed, "amount")
    non_finite = ledger_record(
        session_id,
        uuid4(),
        "non-finite",
        "2026-03-09 00:00:00",
        "transfer",
        "USDC",
        "NaN",
        "0",
        "non-finite",
    )
    with pytest.raises(ValueError, match="Invalid Kraken ledger amount"):
        ledger_decimal(non_finite, "amount")
    with pytest.raises(ValueError, match="exactly one"):
        resolution_metadata(FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL, [])
    with pytest.raises(ValueError, match="not a fiat withdrawal"):
        resolution_metadata(FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL, [weak])
    with pytest.raises(ValueError, match="not one delisting"):
        resolution_metadata(FinancialReviewType.DELISTING_LIQUIDATION, [weak])


def test_financial_review_entity_validation() -> None:
    with pytest.raises(ValueError, match="needs reasons"):
        FinancialReviewSuggestion(
            transformation_issue_id=uuid4(),
            suggestion_type=FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL,
            status=SuggestionStatus.SUGGESTED,
            confidence=ReviewConfidence.LOW,
            reasons=[],
            metadata={},
        )
    with pytest.raises(ValueError, match="exactly one parent"):
        FinancialReviewRecordLink(raw_import_record_id=uuid4(), role="record")
    with pytest.raises(ValueError, match="exactly one parent"):
        FinancialReviewRecordLink(
            raw_import_record_id=uuid4(),
            role="record",
            suggestion_id=uuid4(),
            resolution_id=uuid4(),
        )
    decided = FinancialReviewSuggestion(
        transformation_issue_id=uuid4(),
        suggestion_type=FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL,
        status=SuggestionStatus.REJECTED,
        confidence=ReviewConfidence.LOW,
        reasons=["Evidence is weak"],
        metadata={},
        decided_at=NOW,
        decided_by="user",
        decision_reason="Not my transaction",
    )
    assert decided.decided_at == NOW
    resolution = FinancialReviewResolution(
        transformation_issue_id=uuid4(),
        resolution_type=FinancialReviewType.DELISTING_LIQUIDATION,
        status=ResolutionStatus.CONFIRMED,
        decided_at=NOW,
        decided_by="user",
        reason="Evidence reviewed",
        source="USER_CONFIRMED",
        confidence=None,
        tax_mapping_status=TaxMappingStatus.PENDING,
        metadata={},
    )
    assert resolution.tax_mapping_status is TaxMappingStatus.PENDING
