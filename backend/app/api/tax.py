from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.core.entities import AuditActorType, AuditEvent
from app.core.tax import (
    CLASSIFICATION_RULE_VERSION,
    EXPORT_FORMAT_VERSION,
    EXPORT_FORMAT_VERSIONS,
    FEE_RULE_VERSION,
    FIFO_RULE_VERSION,
    JOURNAL_RULE_VERSION,
    NON_INVENTORY_ASSETS,
    AcquisitionInput,
    DisposalCalculation,
    DisposalInput,
    ExportArtifact,
    ExportKind,
    ExportRun,
    ExportStatus,
    InventoryLot,
    JournalEntryType,
    LotAllocation,
    TaxCalculationRun,
    TaxJournalEntry,
    TaxRecordStatus,
    TaxReportingPeriod,
    TaxReviewCase,
    TaxReviewDecision,
    TaxReviewDecisionValue,
    TaxRuleVersion,
    TaxRunStatus,
    calculate_fifo,
    effective_tax_review_decisions,
    tax_snapshot_hash,
)
from app.core.time import utc_now
from app.core.transformation import (
    AcquisitionLot,
    DisposalEvent,
    DomainProvenance,
    FeeEvent,
    TradeExecution,
    ValuationRequirement,
)
from app.core.valuation import (
    DailyPrice,
    FeeTaxClassification,
    FeeTaxReviewStatus,
    ProviderEvidence,
    ValuationDecision,
    ValuationDecisionStatus,
    exact_decimal_subtract,
    exact_decimal_sum,
)
from app.database.session import get_session
from app.services.tax_exports import (
    ReportAssetRow,
    ReportInventoryRow,
    ReportReviewEvidence,
    TaxReportData,
    csv_bytes,
    export_extension,
    safe_export_path,
    tax_report_pdf,
)

router = APIRouter(prefix="/api", tags=["tax"])
Db = Annotated[Session, Depends(get_session)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=200)]


class TaxCalculationInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    year: int = Field(ge=1970, le=9999)
    fifo_rule_version: str = FIFO_RULE_VERSION
    fee_rule_version: str = FEE_RULE_VERSION
    classification_rule_version: str = CLASSIFICATION_RULE_VERSION
    journal_rule_version: str = JOURNAL_RULE_VERSION
    export_format_version: str = EXPORT_FORMAT_VERSION


class ExportInput(BaseModel):
    tax_calculation_run_id: UUID
    kind: ExportKind


class TaxRunResponse(BaseModel):
    id: str
    status: str
    checked: int
    allocations: int
    journal_entries: int
    reviews: int
    duplicate: bool


class TaxDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str


class TaxCalculationItem(TaxRunResponse):
    duplicate: bool = False
    period_start: date
    period_end: date
    started_at: datetime
    ended_at: datetime | None
    snapshot_hash: str
    rules_fingerprint: str
    fifo_rule_version: str
    fee_rule_version: str
    classification_rule_version: str
    journal_rule_version: str
    export_format_version: str
    supersedes_id: str | None


class TaxCalculationPage(BaseModel):
    items: list[TaxCalculationItem]
    total: int
    offset: int
    limit: int


class InventoryLotItem(BaseModel):
    id: str
    run_id: str
    acquisition_id: str
    asset: str
    original_quantity: str
    remaining_quantity: str
    acquired_at: datetime
    acquisition_value_eur: str
    acquisition_fee_eur: str
    remaining_cost_eur: str
    rule_version: str


class InventoryLotPage(BaseModel):
    items: list[InventoryLotItem]
    total: int
    offset: int
    limit: int


class AllocationItem(BaseModel):
    id: str
    run_id: str
    disposal_id: str
    inventory_lot_id: str
    asset: str
    quantity: str
    order: int
    acquisition_cost_eur: str
    proceeds_eur: str
    fees_eur: str
    gain_loss_eur: str
    acquired_at: datetime
    disposed_at: datetime
    holding_seconds: int
    fifo_rule_version: str
    fee_rule_version: str


class AllocationPage(BaseModel):
    items: list[AllocationItem]
    total: int
    offset: int
    limit: int


class JournalItem(BaseModel):
    id: str
    run_id: str
    occurred_at: datetime
    tax_year: int
    type: str
    asset: str
    quantity: str
    eur_value: str
    proceeds_eur: str | None
    acquisition_cost_eur: str | None
    gain_loss_eur: str | None
    holding_seconds: int | None
    classification: str
    rule_version: str
    status: str
    source_object_type: str
    source_object_id: str
    tax_review_decision_id: str | None = None


class JournalPage(BaseModel):
    items: list[JournalItem]
    total: int
    offset: int
    limit: int


class ExportItem(BaseModel):
    id: str
    export_run_id: str
    kind: str
    format_version: str
    status: str
    file_name: str
    media_type: str
    size_bytes: int
    created_at: datetime
    download_url: str


class ExportPage(BaseModel):
    items: list[ExportItem]
    total: int
    offset: int
    limit: int


class ExportResponse(BaseModel):
    id: str
    status: str
    kind: str
    format_version: str
    artifact_id: str
    download_url: str
    duplicate: bool


class TaxSummaryResponse(BaseModel):
    year: int
    run_id: str | None
    acquisitions: int
    disposals: int
    earn_inflows: int
    realized_gains: str
    realized_losses: str
    net_result: str
    fees: str
    gross_staking_income: str
    staking_fee_candidates: str
    provisional_net_staking_income: str
    staking_fee_included: str
    staking_fee_excluded: str
    staking_fee_open: str
    reviewed_net_staking_income: str
    open_valuations: int
    open_reviews: int
    incomplete_disposals: int
    inventory: dict[str, str]


class TaxReviewDecisionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tax_review_case_id: UUID
    decision: TaxReviewDecisionValue
    reason: str = Field(min_length=1, max_length=1024)


class TaxReviewDecisionBulkInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    tax_review_case_ids: list[UUID] = Field(min_length=1, max_length=200)
    decision: TaxReviewDecisionValue
    reason: str = Field(min_length=1, max_length=1024)


class TaxReviewDecisionResponse(BaseModel):
    id: UUID
    valuation_decision_id: UUID
    tax_review_case_id: UUID
    decision: TaxReviewDecisionValue
    reason: str
    actor_id: str
    decided_at: datetime
    version: int
    supersedes_id: UUID | None
    batch_id: UUID


class TaxReviewDecisionBulkResponse(BaseModel):
    batch_id: UUID
    created_count: int
    superseded_count: int
    decision: TaxReviewDecisionValue


class TaxFeeReviewPage(BaseModel):
    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int
    summary: dict[str, int | str]


@dataclass(frozen=True, kw_only=True)
class PendingTaxReview:
    source_type: str
    source_id: UUID
    asset_code: str
    quantity: Decimal
    occurred_at: datetime
    code: str = "tax_valuation_missing"
    message: str = "Für den Vorgang fehlt eine aufgelöste EUR-Bewertung."


def _list(db: Session, model: type[Any]) -> list[Any]:
    return list(db.scalars(select(model)).all())


def _page(rows: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    return {
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "offset": offset,
        "limit": limit,
    }


def _dict_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        raise ValueError("Persistierte Exportdaten müssen eine Liste sein.")
    result: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("Jeder persistierte Exportdatensatz muss ein Objekt sein.")
        normalized: dict[str, object] = {}
        for key, item_value in item.items():
            if not isinstance(key, str):
                raise ValueError(
                    "Exportdatensätze benötigen ausschließlich Textschlüssel."
                )
            normalized[key] = item_value
        result.append(normalized)
    return result


def _not_found(code: str) -> HTTPException:
    return HTTPException(
        404, detail={"code": code, "message": "Der Datensatz wurde nicht gefunden."}
    )


def _provenance_chain(
    db: Session,
    *,
    domain_object_type: str,
    domain_object_id: UUID,
    valuation_decision_id: UUID | None,
) -> dict[str, Any]:
    links = [
        item
        for item in _list(db, DomainProvenance)
        if item.domain_object_type == domain_object_type
        and item.domain_object_id == domain_object_id
    ]
    decision = (
        db.get(ValuationDecision, valuation_decision_id)
        if valuation_decision_id
        else None
    )
    requirement = (
        db.get(ValuationRequirement, decision.valuation_requirement_id)
        if decision
        else None
    )
    daily_price = (
        db.get(DailyPrice, decision.provider_object_id)
        if decision and decision.provider_object_id
        else None
    )
    evidence = (
        db.get(ProviderEvidence, decision.provider_evidence_id)
        if decision and decision.provider_evidence_id
        else None
    )
    return {
        "import_session_ids": sorted({str(item.import_session_id) for item in links}),
        "raw_import_record_ids": sorted(
            {str(item.raw_import_record_id) for item in links}
        ),
        "transformation_run_ids": sorted(
            {str(item.transformation_run_id) for item in links}
        ),
        "domain_object": {
            "type": domain_object_type,
            "id": str(domain_object_id),
        },
        "valuation_requirement_id": str(requirement.id) if requirement else None,
        "valuation_decision_id": str(decision.id) if decision else None,
        "daily_price_id": str(daily_price.id) if daily_price else None,
        "provider_evidence_id": str(evidence.id) if evidence else None,
    }


def _run_audit(db: Session, run_id: UUID) -> list[dict[str, Any]]:
    return [
        {
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "metadata": event.metadata,
        }
        for event in _list(db, AuditEvent)
        if event.entity_id == run_id
    ]


def _latest_decisions(db: Session) -> dict[UUID, ValuationDecision]:
    decisions: dict[UUID, ValuationDecision] = {}
    for decision in _list(db, ValuationDecision):
        current = decisions.get(decision.domain_object_id)
        if current is None or decision.version > current.version:
            decisions[decision.domain_object_id] = decision
    return {
        object_id: decision
        for object_id, decision in decisions.items()
        if decision.status == ValuationDecisionStatus.RESOLVED
    }


def _effective_review_decisions(db: Session) -> dict[UUID, TaxReviewDecision]:
    try:
        return effective_tax_review_decisions(_list(db, TaxReviewDecision))
    except ValueError as error:
        raise HTTPException(
            409,
            detail={
                "code": "tax_review_decision_history_inconsistent",
                "message": "Die Historie der Reviewentscheidungen ist inkonsistent.",
            },
        ) from error


def _review_decision_row(item: TaxReviewDecision) -> dict[str, Any]:
    return {
        "id": item.id,
        "valuation_decision_id": item.valuation_decision_id,
        "tax_review_case_id": item.source_tax_review_case_id,
        "decision": item.decision,
        "reason": item.reason,
        "actor_id": item.actor_id,
        "decided_at": item.decided_at,
        "version": item.version,
        "supersedes_id": item.supersedes_id,
        "batch_id": item.batch_id,
    }


def _validate_fee_review_case(
    db: Session, case_id: UUID, current: dict[UUID, ValuationDecision]
) -> tuple[TaxReviewCase, ValuationDecision]:
    case = db.get(TaxReviewCase, case_id)
    if case is None:
        raise _not_found("tax_review_case_not_found")
    if (
        case.code != "tax_staking_platform_fee_candidate_review"
        or case.source_object_type != "ValuationDecision"
    ):
        raise HTTPException(
            409,
            detail={
                "code": "tax_review_case_not_decidable_as_staking_fee",
                "message": "Dieser Prüffall ist keine Staking-Plattformgebühr.",
            },
        )
    decision = db.get(ValuationDecision, case.source_object_id)
    if decision is None:
        raise _not_found("valuation_decision_not_found")
    current_decision = current.get(decision.domain_object_id)
    if current_decision is None or current_decision.id != decision.id:
        raise HTTPException(
            409,
            detail={
                "code": "tax_review_valuation_superseded",
                "message": "Die zugehörige Bewertung ist nicht mehr aktuell.",
            },
        )
    if (
        decision.fee_tax_classification
        is not FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
        or decision.fee_tax_review_status is not FeeTaxReviewStatus.REVIEW_REQUIRED
        or decision.fee_value_eur is None
        or decision.fee_value_eur <= 0
    ):
        raise HTTPException(
            409,
            detail={
                "code": "tax_review_valuation_not_fee_candidate",
                "message": "Die Bewertung enthält keinen offenen Gebührenkandidaten.",
            },
        )
    return case, decision


def _create_review_decisions(
    db: Session,
    *,
    case_ids: list[UUID],
    decision_value: TaxReviewDecisionValue,
    reason: str,
) -> tuple[UUID, list[TaxReviewDecision], int]:
    if len(set(case_ids)) != len(case_ids):
        raise HTTPException(
            422,
            detail={
                "code": "tax_review_duplicate_case_ids",
                "message": "Ein Prüffall darf im Batch nur einmal vorkommen.",
            },
        )
    current_valuations = _latest_decisions(db)
    validated = [
        _validate_fee_review_case(db, case_id, current_valuations)
        for case_id in case_ids
    ]
    valuation_ids = [valuation.id for _, valuation in validated]
    if len(set(valuation_ids)) != len(valuation_ids):
        raise HTTPException(
            422,
            detail={
                "code": "tax_review_duplicate_valuation_ids",
                "message": "Eine Bewertung darf im Batch nur einmal vorkommen.",
            },
        )
    effective = _effective_review_decisions(db)
    batch_id = uuid4()
    now = utc_now()
    created: list[TaxReviewDecision] = []
    superseded_count = 0
    for case, valuation in validated:
        previous = effective.get(valuation.id)
        if previous is not None:
            superseded_count += 1
        item = TaxReviewDecision(
            valuation_decision_id=valuation.id,
            source_tax_review_case_id=case.id,
            decision=decision_value,
            reason=reason,
            actor_id="local-user",
            decided_at=now,
            version=(previous.version + 1 if previous else 1),
            supersedes_id=(previous.id if previous else None),
            batch_id=batch_id,
        )
        created.append(item)
    db.add_all(created)
    for item in created:
        db.add(
            AuditEvent(
                occurred_at=now,
                event_type="tax.review_decision_created",
                entity_type="TaxReviewDecision",
                entity_id=item.id,
                actor_type=AuditActorType.USER,
                actor_id="local-user",
                metadata={
                    "valuation_decision_id": str(item.valuation_decision_id),
                    "tax_review_case_id": str(item.source_tax_review_case_id),
                    "decision": item.decision.value,
                    "version": item.version,
                    "batch_id": str(item.batch_id),
                    "supersedes_id": (
                        str(item.supersedes_id) if item.supersedes_id else None
                    ),
                },
            )
        )
    db.commit()
    return batch_id, created, superseded_count


@router.post("/tax-review-decisions", response_model=TaxReviewDecisionResponse)
def create_tax_review_decision(data: TaxReviewDecisionInput, db: Db) -> dict[str, Any]:
    _, created, _ = _create_review_decisions(
        db,
        case_ids=[data.tax_review_case_id],
        decision_value=data.decision,
        reason=data.reason,
    )
    return _review_decision_row(created[0])


@router.post("/tax-review-decisions/bulk", response_model=TaxReviewDecisionBulkResponse)
def create_tax_review_decisions_bulk(
    data: TaxReviewDecisionBulkInput, db: Db
) -> dict[str, Any]:
    batch_id, created, superseded_count = _create_review_decisions(
        db,
        case_ids=data.tax_review_case_ids,
        decision_value=data.decision,
        reason=data.reason,
    )
    return {
        "batch_id": batch_id,
        "created_count": len(created),
        "superseded_count": superseded_count,
        "decision": data.decision,
    }


@router.get("/tax-review-decisions", response_model=TaxFeeReviewPage)
def tax_review_decision_list(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    year: int | None = Query(default=None, ge=1970, le=9999),
    status: Literal["open", "resolved", "all"] = "all",
    asset: str | None = None,
    decision: TaxReviewDecisionValue | None = None,
) -> dict[str, Any]:
    effective = _effective_review_decisions(db)
    history_by_valuation: dict[UUID, list[TaxReviewDecision]] = {}
    for item in _list(db, TaxReviewDecision):
        history_by_valuation.setdefault(item.valuation_decision_id, []).append(item)
    rows: list[dict[str, Any]] = []
    seen_valuations: set[UUID] = set()
    for case in sorted(
        _list(db, TaxReviewCase), key=lambda item: (item.occurred_at, item.id.hex)
    ):
        if (
            case.code != "tax_staking_platform_fee_candidate_review"
            or case.source_object_type != "ValuationDecision"
            or case.source_object_id in seen_valuations
        ):
            continue
        valuation = db.get(ValuationDecision, case.source_object_id)
        if valuation is None or valuation.fee_value_eur is None:
            continue
        seen_valuations.add(valuation.id)
        current = effective.get(valuation.id)
        state = "resolved" if current else "open"
        if year is not None and valuation.price_date.year != year:
            continue
        if status != "all" and status != state:
            continue
        if asset is not None and valuation.asset_code != asset.upper():
            continue
        if decision is not None and (
            current is None or current.decision is not decision
        ):
            continue
        history = sorted(
            history_by_valuation.get(valuation.id, []), key=lambda item: item.version
        )
        rows.append(
            {
                "tax_review_case_id": str(case.id),
                "valuation_decision_id": str(valuation.id),
                "asset": valuation.asset_code,
                "date": valuation.price_date,
                "fee_quantity": str(valuation.fee_quantity or Decimal("0")),
                "fee_value_eur": str(valuation.fee_value_eur),
                "status": state,
                "decision": current.decision.value if current else None,
                "reason": current.reason if current else None,
                "actor_id": current.actor_id if current else None,
                "decided_at": current.decided_at if current else None,
                "version": current.version if current else None,
                "batch_id": str(current.batch_id) if current else None,
                "history": [_review_decision_row(item) for item in history],
            }
        )
    page = _page(rows, offset, limit)
    page["summary"] = {
        "open_count": sum(row["status"] == "open" for row in rows),
        "decided_count": sum(row["status"] == "resolved" for row in rows),
        "open_total_eur": str(
            exact_decimal_sum(
                tuple(
                    Decimal(str(row["fee_value_eur"]))
                    for row in rows
                    if row["status"] == "open"
                )
            )
        ),
        "included_total_eur": str(
            exact_decimal_sum(
                tuple(
                    Decimal(str(row["fee_value_eur"]))
                    for row in rows
                    if row["decision"]
                    == TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN.value
                )
            )
        ),
        "excluded_total_eur": str(
            exact_decimal_sum(
                tuple(
                    Decimal(str(row["fee_value_eur"]))
                    for row in rows
                    if row["decision"]
                    == TaxReviewDecisionValue.EXCLUDE_FROM_WERBUNGSKOSTEN.value
                )
            )
        ),
    }
    return page


def _fee_values(
    db: Session, decisions: dict[UUID, ValuationDecision]
) -> dict[UUID, Decimal]:
    values: dict[UUID, Decimal] = {}
    for fee in _list(db, FeeEvent):
        decision = decisions.get(fee.id)
        if decision is not None:
            values[fee.related_object_id] = exact_decimal_sum(
                (values.get(fee.related_object_id, Decimal("0")), decision.eur_value)
            )
    return values


def _tax_inputs(
    db: Session, period: TaxReportingPeriod
) -> tuple[list[AcquisitionInput], list[DisposalInput], list[PendingTaxReview]]:
    decisions = _latest_decisions(db)
    review_decisions = _effective_review_decisions(db)
    fee_values = _fee_values(db, decisions)
    trades_by_external = {
        trade.external_id: trade for trade in _list(db, TradeExecution)
    }
    acquisitions: list[AcquisitionInput] = []
    disposals: list[DisposalInput] = []
    missing: list[PendingTaxReview] = []
    for item in _list(db, AcquisitionLot):
        if (
            item.occurred_at.date() > period.end
            or item.asset_code in NON_INVENTORY_ASSETS
        ):
            continue
        decision = decisions.get(item.id)
        if decision is None:
            missing.append(
                PendingTaxReview(
                    source_type="AcquisitionLot",
                    source_id=item.id,
                    asset_code=item.asset_code,
                    quantity=item.quantity,
                    occurred_at=item.occurred_at,
                )
            )
            continue
        trade = trades_by_external.get(item.external_id)
        fee = fee_values.get(trade.id, Decimal("0")) if trade else Decimal("0")
        is_staking = "staking" in item.acquisition_type.value
        if is_staking and decision.gross_income_eur is None:
            missing.append(
                PendingTaxReview(
                    source_type="AcquisitionLot",
                    source_id=item.id,
                    asset_code=item.asset_code,
                    quantity=item.quantity,
                    occurred_at=item.occurred_at,
                    code="tax_reward_gross_income_missing",
                    message=(
                        "Die historische Rewardbewertung weist keinen "
                        "separaten Bruttoertrag aus."
                    ),
                )
            )
        effective_review = review_decisions.get(decision.id)
        if (
            decision.fee_tax_review_status is FeeTaxReviewStatus.REVIEW_REQUIRED
            and effective_review is None
        ):
            missing.append(
                PendingTaxReview(
                    source_type="ValuationDecision",
                    source_id=decision.id,
                    asset_code=item.asset_code,
                    quantity=item.fee_quantity,
                    occurred_at=item.occurred_at,
                    code="tax_staking_platform_fee_candidate_review",
                    message=(
                        "Die steuerliche Abziehbarkeit der einbehaltenen "
                        "Staking-Plattformgebühr ist zu prüfen."
                    ),
                )
            )
        acquisitions.append(
            AcquisitionInput(
                acquisition_id=item.id,
                asset_code=item.asset_code,
                quantity=item.quantity,
                acquired_at=item.occurred_at,
                value_eur=(decision.net_acquisition_value_eur or decision.eur_value),
                fee_eur=fee,
                valuation_decision_id=decision.id,
                acquisition_type=item.acquisition_type.value,
                gross_income_eur=decision.gross_income_eur,
                platform_fee_candidate_eur=(decision.fee_value_eur or Decimal("0")),
                platform_fee_decision=(
                    effective_review.decision if effective_review else None
                ),
                tax_review_decision_id=(
                    effective_review.id if effective_review else None
                ),
                tax_review_decision_version=(
                    effective_review.version if effective_review else None
                ),
            )
        )
    for item in _list(db, DisposalEvent):
        if not period.start <= item.occurred_at.date() <= period.end:
            continue
        decision = decisions.get(item.id)
        if decision is None:
            missing.append(
                PendingTaxReview(
                    source_type="DisposalEvent",
                    source_id=item.id,
                    asset_code=item.asset_code,
                    quantity=item.quantity,
                    occurred_at=item.occurred_at,
                )
            )
            continue
        disposals.append(
            DisposalInput(
                disposal_id=item.id,
                asset_code=item.asset_code,
                quantity=item.quantity,
                disposed_at=item.occurred_at,
                proceeds_eur=decision.eur_value,
                fee_eur=fee_values.get(item.trade_execution_id, Decimal("0")),
                valuation_decision_id=decision.id,
                disposal_type=item.disposal_type.value,
            )
        )
    for fee in _list(db, FeeEvent):
        if (
            fee.asset_code != "EUR"
            and period.start <= fee.occurred_at.date() <= period.end
        ):
            missing.append(
                PendingTaxReview(
                    source_type="FeeEvent",
                    source_id=fee.id,
                    asset_code=fee.asset_code,
                    quantity=fee.quantity,
                    occurred_at=fee.occurred_at,
                    code="tax_crypto_fee_requires_disposal_review",
                    message=(
                        "Die Kryptogebühr benötigt einen nachgewiesenen Bestandsabgang."
                    ),
                )
            )
    return acquisitions, disposals, missing


@router.post("/tax-calculations", response_model=TaxRunResponse)
def create_tax_calculation(data: TaxCalculationInput, db: Db) -> dict[str, Any]:
    period = TaxReportingPeriod.for_year(data.year)
    rules = TaxRuleVersion(
        fifo=data.fifo_rule_version,
        fees=data.fee_rule_version,
        classification=data.classification_rule_version,
        journal=data.journal_rule_version,
        export=data.export_format_version,
    )
    acquisitions, disposals, missing = _tax_inputs(db, period)
    snapshot_hash = tax_snapshot_hash(acquisitions, disposals)
    if missing:
        marker = "\n".join(
            f"M|{item.source_type}|{item.source_id}|{item.asset_code}|"
            f"{item.quantity}|{item.occurred_at.isoformat()}|{item.code}"
            for item in sorted(
                missing,
                key=lambda item: (
                    item.occurred_at,
                    item.source_type,
                    item.source_id.hex,
                ),
            )
        )
        snapshot_hash = sha256(f"{snapshot_hash}\n{marker}".encode()).hexdigest()
    existing = next(
        (
            run
            for run in _list(db, TaxCalculationRun)
            if run.period_start == period.start
            and run.period_end == period.end
            and run.snapshot_hash == snapshot_hash
            and run.rules_fingerprint == rules.fingerprint
            and run.status
            in {TaxRunStatus.COMPLETED, TaxRunStatus.COMPLETED_WITH_REVIEW}
        ),
        None,
    )
    if existing:
        return _run_response(existing, duplicate=True)
    previous = max(
        (
            run
            for run in _list(db, TaxCalculationRun)
            if run.period_start == period.start
            and run.period_end == period.end
            and run.status
            in {TaxRunStatus.COMPLETED, TaxRunStatus.COMPLETED_WITH_REVIEW}
        ),
        key=lambda run: (run.started_at, run.id.hex),
        default=None,
    )
    now = utc_now()
    run = TaxCalculationRun(
        period_start=period.start,
        period_end=period.end,
        snapshot_hash=snapshot_hash,
        rules_fingerprint=rules.fingerprint,
        status=TaxRunStatus.PROCESSING,
        started_at=now,
        fifo_rule_version=rules.fifo,
        fee_rule_version=rules.fees,
        classification_rule_version=rules.classification,
        journal_rule_version=rules.journal,
        export_format_version=rules.export,
        checked_events=len(acquisitions) + len(disposals) + len(missing),
        supersedes_id=previous.id if previous else None,
    )
    db.add(run)
    db.add(
        AuditEvent(
            occurred_at=now,
            event_type="tax.calculation_created",
            entity_type="TaxCalculationRun",
            entity_id=run.id,
            actor_type=AuditActorType.SYSTEM,
            actor_id="tax-engine",
            metadata={
                "period_start": period.start.isoformat(),
                "period_end": period.end.isoformat(),
                "rules_fingerprint": rules.fingerprint,
            },
        )
    )
    result = calculate_fifo(
        run_id=run.id,
        period=period,
        rules=rules,
        acquisitions=acquisitions,
        disposals=disposals,
    )
    db.add_all(
        [
            *result.lots,
            *result.allocations,
            *result.calculations,
            *result.journal,
            *result.reviews,
        ]
    )
    for item in missing:
        review = TaxReviewCase(
            tax_calculation_run_id=run.id,
            code=item.code,
            message=item.message,
            source_object_type=item.source_type,
            source_object_id=item.source_id,
            occurred_at=item.occurred_at,
        )
        db.add(review)
        db.add(
            TaxJournalEntry(
                tax_calculation_run_id=run.id,
                occurred_at=item.occurred_at,
                tax_year=item.occurred_at.year,
                entry_type=JournalEntryType.REVIEW,
                asset_code=item.asset_code,
                quantity=item.quantity,
                eur_value=Decimal("0"),
                proceeds_eur=None,
                acquisition_cost_eur=None,
                gain_loss_eur=None,
                holding_seconds=None,
                classification=review.message,
                rule_version=rules.journal,
                status=TaxRecordStatus.REVIEW_REQUIRED,
                source_object_type=item.source_type,
                source_object_id=item.source_id,
            )
        )
    run.created_allocations = len(result.allocations)
    run.created_journal_entries = len(result.journal) + len(missing)
    run.review_count = len(result.reviews) + len(missing)
    run.status = (
        TaxRunStatus.COMPLETED_WITH_REVIEW
        if run.review_count
        else TaxRunStatus.COMPLETED
    )
    run.ended_at = utc_now()
    if previous is not None:
        previous.status = TaxRunStatus.SUPERSEDED
        db.add(
            AuditEvent(
                occurred_at=run.ended_at,
                event_type="tax.calculation_superseded",
                entity_type="TaxCalculationRun",
                entity_id=previous.id,
                actor_type=AuditActorType.SYSTEM,
                actor_id="tax-engine",
                metadata={"successor_id": str(run.id)},
            )
        )
    db.add(
        AuditEvent(
            occurred_at=run.ended_at,
            event_type=(
                "tax.calculation_completed_with_review"
                if run.review_count
                else "tax.calculation_completed"
            ),
            entity_type="TaxCalculationRun",
            entity_id=run.id,
            actor_type=AuditActorType.SYSTEM,
            actor_id="tax-engine",
            metadata={
                "snapshot_hash": snapshot_hash,
                "rules_fingerprint": rules.fingerprint,
                "reviews": run.review_count,
            },
        )
    )
    db.commit()
    return _run_response(run, duplicate=False)


def _run_response(run: TaxCalculationRun, *, duplicate: bool) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status.value,
        "checked": run.checked_events,
        "allocations": run.created_allocations,
        "journal_entries": run.created_journal_entries,
        "reviews": run.review_count,
        "duplicate": duplicate,
    }


def _run_row(run: TaxCalculationRun) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "period_start": run.period_start,
        "period_end": run.period_end,
        "status": run.status.value,
        "checked": run.checked_events,
        "allocations": run.created_allocations,
        "journal_entries": run.created_journal_entries,
        "reviews": run.review_count,
        "started_at": run.started_at,
        "ended_at": run.ended_at,
        "snapshot_hash": run.snapshot_hash,
        "rules_fingerprint": run.rules_fingerprint,
        "fifo_rule_version": run.fifo_rule_version,
        "fee_rule_version": run.fee_rule_version,
        "classification_rule_version": run.classification_rule_version,
        "journal_rule_version": run.journal_rule_version,
        "export_format_version": run.export_format_version,
        "supersedes_id": str(run.supersedes_id) if run.supersedes_id else None,
    }


@router.get("/tax-calculations", response_model=TaxCalculationPage)
def tax_calculations(
    db: Db, offset: Offset = 0, limit: Limit = 100, year: int | None = None
) -> dict[str, Any]:
    rows = [
        _run_row(run)
        for run in _list(db, TaxCalculationRun)
        if year is None or run.period_start.year == year
    ]
    rows.sort(key=lambda row: (str(row["started_at"]), str(row["id"])), reverse=True)
    return _page(rows, offset, limit)


@router.get("/tax-calculations/{item_id}", response_model=TaxDetailResponse)
def tax_calculation_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    run = db.get(TaxCalculationRun, item_id)
    if run is None:
        raise _not_found("tax_calculation_not_found")
    row = _run_row(run)
    row["rules"] = {
        "fingerprint": run.rules_fingerprint,
        "fifo": run.fifo_rule_version,
        "fees": run.fee_rule_version,
        "classification": run.classification_rule_version,
        "journal": run.journal_rule_version,
        "export": run.export_format_version,
    }
    row["audit"] = [
        {
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "metadata": event.metadata,
        }
        for event in _list(db, AuditEvent)
        if event.entity_id == run.id
    ]
    return row


def _latest_run(db: Session, year: int | None = None) -> TaxCalculationRun | None:
    runs = [
        run
        for run in _list(db, TaxCalculationRun)
        if run.status in {TaxRunStatus.COMPLETED, TaxRunStatus.COMPLETED_WITH_REVIEW}
        and (year is None or run.period_start.year == year)
    ]
    return max(runs, key=lambda run: (run.started_at, run.id.hex), default=None)


@router.get("/inventory-lots", response_model=InventoryLotPage)
def inventory_lot_list(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    year: int | None = None,
    asset: str | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    run = db.get(TaxCalculationRun, run_id) if run_id else _latest_run(db, year)
    rows = [
        {
            "id": str(item.id),
            "run_id": str(item.tax_calculation_run_id),
            "acquisition_id": str(item.acquisition_lot_id),
            "asset": item.asset_code,
            "original_quantity": str(item.original_quantity),
            "remaining_quantity": str(item.remaining_quantity),
            "acquired_at": item.acquired_at,
            "acquisition_value_eur": str(item.acquisition_value_eur),
            "acquisition_fee_eur": str(item.acquisition_fee_eur),
            "remaining_cost_eur": str(item.remaining_cost_eur),
            "rule_version": item.rule_version,
        }
        for item in _list(db, InventoryLot)
        if run
        and item.tax_calculation_run_id == run.id
        and (asset is None or item.asset_code == asset.upper())
    ]
    rows.sort(key=lambda row: (str(row["acquired_at"]), str(row["id"])))
    return _page(rows, offset, limit)


@router.get("/inventory-lots/{item_id}", response_model=TaxDetailResponse)
def inventory_lot_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    lot = db.get(InventoryLot, item_id)
    if lot is None:
        raise _not_found("inventory_lot_not_found")
    item: dict[str, Any] = {
        "id": str(lot.id),
        "run_id": str(lot.tax_calculation_run_id),
        "acquisition_id": str(lot.acquisition_lot_id),
        "asset": lot.asset_code,
        "original_quantity": str(lot.original_quantity),
        "remaining_quantity": str(lot.remaining_quantity),
        "acquired_at": lot.acquired_at,
        "acquisition_value_eur": str(lot.acquisition_value_eur),
        "acquisition_fee_eur": str(lot.acquisition_fee_eur),
        "remaining_cost_eur": str(lot.remaining_cost_eur),
        "valuation_decision_id": str(lot.valuation_decision_id),
        "rule_version": lot.rule_version,
    }
    item["allocations"] = [
        str(allocation.id)
        for allocation in _list(db, LotAllocation)
        if allocation.inventory_lot_id == lot.id
    ]
    item["provenance"] = _provenance_chain(
        db,
        domain_object_type="AcquisitionLot",
        domain_object_id=lot.acquisition_lot_id,
        valuation_decision_id=lot.valuation_decision_id,
    )
    item["provenance"]["tax_calculation_run_id"] = str(lot.tax_calculation_run_id)
    item["audit"] = _run_audit(db, lot.tax_calculation_run_id)
    return item


@router.get("/lot-allocations", response_model=AllocationPage)
def allocation_list(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    year: int | None = None,
    asset: str | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    run = db.get(TaxCalculationRun, run_id) if run_id else _latest_run(db, year)
    lots = {item.id: item for item in _list(db, InventoryLot)}
    rows = [
        {
            "id": str(item.id),
            "run_id": str(item.tax_calculation_run_id),
            "disposal_id": str(item.disposal_event_id),
            "inventory_lot_id": str(item.inventory_lot_id),
            "asset": lots[item.inventory_lot_id].asset_code,
            "quantity": str(item.allocated_quantity),
            "order": item.allocation_order,
            "acquisition_cost_eur": str(item.acquisition_cost_eur),
            "proceeds_eur": str(item.disposal_proceeds_eur),
            "fees_eur": str(item.disposal_fee_eur),
            "gain_loss_eur": str(item.gain_loss_eur),
            "acquired_at": item.acquired_at,
            "disposed_at": item.disposed_at,
            "holding_seconds": item.holding_seconds,
            "fifo_rule_version": item.fifo_rule_version,
            "fee_rule_version": item.fee_rule_version,
        }
        for item in _list(db, LotAllocation)
        if run
        and item.tax_calculation_run_id == run.id
        and (asset is None or lots[item.inventory_lot_id].asset_code == asset.upper())
    ]
    rows.sort(
        key=lambda row: (str(row["disposed_at"]), int(row["order"]), str(row["id"]))
    )
    return _page(rows, offset, limit)


@router.get("/lot-allocations/{item_id}", response_model=TaxDetailResponse)
def allocation_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    item = db.get(LotAllocation, item_id)
    if item is None:
        raise _not_found("lot_allocation_not_found")
    lot = db.get(InventoryLot, item.inventory_lot_id)
    disposal_decision = _latest_decisions(db).get(item.disposal_event_id)
    row: dict[str, Any] = {
        "id": str(item.id),
        "run_id": str(item.tax_calculation_run_id),
        "disposal_id": str(item.disposal_event_id),
        "inventory_lot_id": str(item.inventory_lot_id),
        "asset": lot.asset_code if lot else None,
        "quantity": str(item.allocated_quantity),
        "order": item.allocation_order,
        "acquisition_cost_eur": str(item.acquisition_cost_eur),
        "proceeds_eur": str(item.disposal_proceeds_eur),
        "fees_eur": str(item.disposal_fee_eur),
        "gain_loss_eur": str(item.gain_loss_eur),
        "acquired_at": item.acquired_at,
        "disposed_at": item.disposed_at,
        "holding_seconds": item.holding_seconds,
        "fifo_rule_version": item.fifo_rule_version,
        "fee_rule_version": item.fee_rule_version,
    }
    row["provenance"] = {
        "inventory_lot_id": str(item.inventory_lot_id),
        "acquisition": (
            _provenance_chain(
                db,
                domain_object_type="AcquisitionLot",
                domain_object_id=lot.acquisition_lot_id,
                valuation_decision_id=lot.valuation_decision_id,
            )
            if lot
            else None
        ),
        "disposal": _provenance_chain(
            db,
            domain_object_type="DisposalEvent",
            domain_object_id=item.disposal_event_id,
            valuation_decision_id=(disposal_decision.id if disposal_decision else None),
        ),
        "tax_calculation_run_id": str(item.tax_calculation_run_id),
    }
    row["audit"] = _run_audit(db, item.tax_calculation_run_id)
    return row


@router.get("/tax-journal", response_model=JournalPage)
def journal_list(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    year: int | None = None,
    asset: str | None = None,
    entry_type: JournalEntryType | None = None,
    status: TaxRecordStatus | None = None,
    review: bool | None = None,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    run = db.get(TaxCalculationRun, run_id) if run_id else _latest_run(db, year)
    rows = [
        {
            "id": str(item.id),
            "run_id": str(item.tax_calculation_run_id),
            "occurred_at": item.occurred_at,
            "tax_year": item.tax_year,
            "type": item.entry_type.value,
            "asset": item.asset_code,
            "quantity": str(item.quantity),
            "eur_value": str(item.eur_value),
            "proceeds_eur": (
                str(item.proceeds_eur) if item.proceeds_eur is not None else None
            ),
            "acquisition_cost_eur": (
                str(item.acquisition_cost_eur)
                if item.acquisition_cost_eur is not None
                else None
            ),
            "gain_loss_eur": (
                str(item.gain_loss_eur) if item.gain_loss_eur is not None else None
            ),
            "holding_seconds": item.holding_seconds,
            "classification": item.classification,
            "rule_version": item.rule_version,
            "status": item.status.value,
            "source_object_type": item.source_object_type,
            "source_object_id": str(item.source_object_id),
            "tax_review_decision_id": (
                str(item.tax_review_decision_id)
                if item.tax_review_decision_id
                else None
            ),
        }
        for item in _list(db, TaxJournalEntry)
        if run
        and item.tax_calculation_run_id == run.id
        and (asset is None or item.asset_code == asset.upper())
        and (entry_type is None or item.entry_type == entry_type)
        and (status is None or item.status == status)
        and (
            review is None or (item.status == TaxRecordStatus.REVIEW_REQUIRED) == review
        )
    ]
    rows.sort(key=lambda row: (str(row["occurred_at"]), str(row["id"])))
    return _page(rows, offset, limit)


@router.get("/tax-journal/{item_id}", response_model=TaxDetailResponse)
def journal_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    item = db.get(TaxJournalEntry, item_id)
    if item is None:
        raise _not_found("tax_journal_entry_not_found")
    row: dict[str, Any] = {
        "id": str(item.id),
        "run_id": str(item.tax_calculation_run_id),
        "occurred_at": item.occurred_at,
        "tax_year": item.tax_year,
        "type": item.entry_type.value,
        "asset": item.asset_code,
        "quantity": str(item.quantity),
        "eur_value": str(item.eur_value),
        "proceeds_eur": (
            str(item.proceeds_eur) if item.proceeds_eur is not None else None
        ),
        "acquisition_cost_eur": (
            str(item.acquisition_cost_eur)
            if item.acquisition_cost_eur is not None
            else None
        ),
        "gain_loss_eur": (
            str(item.gain_loss_eur) if item.gain_loss_eur is not None else None
        ),
        "holding_seconds": item.holding_seconds,
        "classification": item.classification,
        "rule_version": item.rule_version,
        "status": item.status.value,
        "source_object_type": item.source_object_type,
        "source_object_id": str(item.source_object_id),
        "tax_review_decision_id": (
            str(item.tax_review_decision_id) if item.tax_review_decision_id else None
        ),
    }
    row["provenance"] = _provenance_chain(
        db,
        domain_object_type=item.source_object_type,
        domain_object_id=item.source_object_id,
        valuation_decision_id=item.valuation_decision_id,
    )
    row["provenance"]["lot_allocation_id"] = (
        str(item.lot_allocation_id) if item.lot_allocation_id else None
    )
    row["provenance"]["tax_calculation_run_id"] = str(item.tax_calculation_run_id)
    row["audit"] = _run_audit(db, item.tax_calculation_run_id)
    return row


@router.get("/tax-summary", response_model=TaxSummaryResponse)
def tax_summary(db: Db, year: int = Query(ge=1970, le=9999)) -> dict[str, Any]:
    run = _latest_run(db, year)
    journal = [
        item
        for item in _list(db, TaxJournalEntry)
        if run and item.tax_calculation_run_id == run.id
    ]
    calculations = [
        item
        for item in _list(db, DisposalCalculation)
        if run and item.tax_calculation_run_id == run.id
    ]
    lots = [
        item
        for item in _list(db, InventoryLot)
        if run and item.tax_calculation_run_id == run.id
    ]
    gains = exact_decimal_sum(
        tuple(
            item.gain_loss_eur or Decimal("0")
            for item in journal
            if item.entry_type == JournalEntryType.REALIZED_GAIN
            and item.gain_loss_eur
            and item.gain_loss_eur > 0
        )
    )
    losses = exact_decimal_sum(
        tuple(
            (item.gain_loss_eur or Decimal("0")).copy_negate()
            for item in journal
            if item.entry_type == JournalEntryType.REALIZED_LOSS
            and item.gain_loss_eur
            and item.gain_loss_eur < 0
        )
    )
    fees = exact_decimal_sum(tuple(item.fees_eur for item in calculations))
    current_decisions = tuple(_latest_decisions(db).values())
    gross_staking_income = exact_decimal_sum(
        tuple(
            item.gross_income_eur
            for item in current_decisions
            if item.price_date.year == year and item.gross_income_eur is not None
        )
    )
    staking_fee_candidates = exact_decimal_sum(
        tuple(
            item.fee_value_eur
            for item in current_decisions
            if item.price_date.year == year
            and item.fee_value_eur is not None
            and item.fee_tax_classification
            is FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
        )
    )
    effective_reviews = _effective_review_decisions(db)
    included_values: list[Decimal] = []
    excluded_values: list[Decimal] = []
    open_values: list[Decimal] = []
    for item in current_decisions:
        if (
            item.price_date.year != year
            or item.fee_value_eur is None
            or item.fee_tax_classification
            is not FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
        ):
            continue
        review_decision = effective_reviews.get(item.id)
        if review_decision is None:
            open_values.append(item.fee_value_eur)
        elif (
            review_decision.decision is TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN
        ):
            included_values.append(item.fee_value_eur)
        else:
            excluded_values.append(item.fee_value_eur)
    staking_fee_included = exact_decimal_sum(included_values)
    staking_fee_excluded = exact_decimal_sum(excluded_values)
    staking_fee_open = exact_decimal_sum(open_values)
    inventory: dict[str, Decimal] = {}
    for lot in lots:
        inventory[lot.asset_code] = exact_decimal_sum(
            (inventory.get(lot.asset_code, Decimal("0")), lot.remaining_quantity)
        )
    resolved_requirement_ids = {
        decision.valuation_requirement_id for decision in _latest_decisions(db).values()
    }
    open_valuations = sum(
        requirement.id not in resolved_requirement_ids
        for requirement in _list(db, ValuationRequirement)
        if requirement.valuation_at.year == year
    )
    return {
        "year": year,
        "run_id": str(run.id) if run else None,
        "acquisitions": sum(
            item.entry_type == JournalEntryType.ACQUISITION for item in journal
        ),
        "disposals": len(calculations),
        "earn_inflows": sum(
            item.entry_type == JournalEntryType.EARN_INFLOW for item in journal
        ),
        "realized_gains": str(gains),
        "realized_losses": str(losses),
        "net_result": str(exact_decimal_subtract(gains, losses)),
        "fees": str(fees),
        "gross_staking_income": str(gross_staking_income),
        "staking_fee_candidates": str(staking_fee_candidates),
        "provisional_net_staking_income": str(
            exact_decimal_subtract(gross_staking_income, staking_fee_candidates)
        ),
        "staking_fee_included": str(staking_fee_included),
        "staking_fee_excluded": str(staking_fee_excluded),
        "staking_fee_open": str(staking_fee_open),
        "reviewed_net_staking_income": str(
            exact_decimal_subtract(gross_staking_income, staking_fee_included)
        ),
        "open_valuations": open_valuations,
        "open_reviews": run.review_count if run else 0,
        "incomplete_disposals": sum(
            item.status == TaxRecordStatus.REVIEW_REQUIRED for item in calculations
        ),
        "inventory": {
            asset: str(quantity) for asset, quantity in sorted(inventory.items())
        },
    }


EXPORT_COLUMNS: dict[ExportKind, tuple[str, ...]] = {
    ExportKind.TAX_JOURNAL_CSV: (
        "id",
        "occurred_at",
        "tax_year",
        "type",
        "asset",
        "quantity",
        "eur_value",
        "proceeds_eur",
        "acquisition_cost_eur",
        "gain_loss_eur",
        "rule_version",
        "status",
        "source_object_id",
        "tax_review_decision_id",
    ),
    ExportKind.FIFO_ALLOCATIONS_CSV: (
        "id",
        "disposal_id",
        "inventory_lot_id",
        "asset",
        "quantity",
        "order",
        "acquisition_cost_eur",
        "proceeds_eur",
        "fees_eur",
        "gain_loss_eur",
        "acquired_at",
        "disposed_at",
        "holding_seconds",
        "fifo_rule_version",
        "fee_rule_version",
    ),
    ExportKind.INVENTORY_CSV: (
        "id",
        "acquisition_id",
        "asset",
        "original_quantity",
        "remaining_quantity",
        "acquired_at",
        "acquisition_value_eur",
        "acquisition_fee_eur",
        "remaining_cost_eur",
        "rule_version",
    ),
    ExportKind.VALUATION_EVIDENCE_CSV: (
        "id",
        "asset",
        "date",
        "method",
        "method_version",
        "unit_price_eur",
        "quantity",
        "eur_value",
        "gross_quantity",
        "fee_quantity",
        "net_quantity",
        "gross_income_eur",
        "fee_value_eur",
        "net_acquisition_value_eur",
        "valuation_basis",
        "fee_tax_classification",
        "fee_tax_review_status",
        "provider",
        "version",
    ),
    ExportKind.REVIEWS_CSV: (
        "id",
        "code",
        "message",
        "source_object_type",
        "source_object_id",
        "occurred_at",
        "valuation_decision_id",
        "fee_value_eur",
        "review_status",
        "decision",
        "reason",
        "actor_id",
        "decided_at",
        "version",
        "batch_id",
    ),
    ExportKind.ANNUAL_SUMMARY_CSV: (
        "year",
        "acquisitions",
        "disposals",
        "earn_inflows",
        "realized_gains",
        "realized_losses",
        "net_result",
        "fees",
        "gross_staking_income",
        "staking_fee_candidates",
        "provisional_net_staking_income",
        "staking_fee_included",
        "staking_fee_excluded",
        "staking_fee_open",
        "reviewed_net_staking_income",
        "open_valuations",
        "open_reviews",
        "incomplete_disposals",
    ),
}


def _tax_report_data(
    db: Session, run: TaxCalculationRun, created_at: datetime
) -> TaxReportData:
    valuations = [
        item
        for item in _latest_decisions(db).values()
        if run.period_start <= item.price_date <= run.period_end
        and item.gross_income_eur is not None
        and item.net_acquisition_value_eur is not None
    ]
    by_asset: dict[str, list[ValuationDecision]] = {}
    for item in valuations:
        by_asset.setdefault(item.asset_code, []).append(item)
    asset_rows = tuple(
        ReportAssetRow(
            asset=asset,
            inflows=len(items),
            gross_eur=exact_decimal_sum(
                tuple(item.gross_income_eur or Decimal("0") for item in items)
            ),
            fee_eur=exact_decimal_sum(
                tuple(item.fee_value_eur or Decimal("0") for item in items)
            ),
            net_eur=exact_decimal_sum(
                tuple(item.net_acquisition_value_eur or Decimal("0") for item in items)
            ),
        )
        for asset, items in sorted(by_asset.items())
    )
    inventory_by_asset: dict[str, list[InventoryLot]] = {}
    for item in _list(db, InventoryLot):
        if item.tax_calculation_run_id == run.id:
            inventory_by_asset.setdefault(item.asset_code, []).append(item)
    inventory_rows = tuple(
        ReportInventoryRow(
            asset=asset,
            quantity=exact_decimal_sum(
                tuple(item.remaining_quantity for item in items)
            ),
            cost_eur=exact_decimal_sum(
                tuple(item.remaining_cost_eur for item in items)
            ),
        )
        for asset, items in sorted(inventory_by_asset.items())
    )
    effective = _effective_review_decisions(db)
    relevant = [
        (item, effective.get(item.id))
        for item in valuations
        if item.fee_tax_classification is FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
    ]
    included = [
        (valuation, decision)
        for valuation, decision in relevant
        if decision is not None
        and decision.decision is TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN
    ]
    excluded = [
        decision
        for _, decision in relevant
        if decision is not None
        and decision.decision is TaxReviewDecisionValue.EXCLUDE_FROM_WERBUNGSKOSTEN
    ]
    decisions = [decision for _, decision in relevant if decision is not None]
    review = ReportReviewEvidence(
        candidates=len(relevant),
        included=len(included),
        excluded=len(excluded),
        open_count=sum(decision is None for _, decision in relevant),
        included_eur=exact_decimal_sum(
            tuple(valuation.fee_value_eur or Decimal("0") for valuation, _ in included)
        ),
        batch_ids=tuple(sorted({str(item.batch_id) for item in decisions})),
        versions=tuple(sorted({item.version for item in decisions})),
        actors=tuple(sorted({item.actor_id for item in decisions})),
        decided_from=min((item.decided_at for item in decisions), default=None),
        decided_to=max((item.decided_at for item in decisions), default=None),
        reasons=tuple(sorted({item.reason for item in decisions})),
    )
    gross = exact_decimal_sum(tuple(item.gross_eur for item in asset_rows))
    net = exact_decimal_sum(tuple(item.net_eur for item in asset_rows))
    return TaxReportData(
        run_id=str(run.id),
        status=run.status.value,
        period_start=run.period_start,
        period_end=run.period_end,
        created_at=created_at,
        snapshot_hash=run.snapshot_hash,
        rules_fingerprint=run.rules_fingerprint,
        rules=TaxRuleVersion(
            fifo=run.fifo_rule_version,
            fees=run.fee_rule_version,
            classification=run.classification_rule_version,
            journal=run.journal_rule_version,
            export=run.export_format_version,
        ),
        gross_staking_income=gross,
        staking_fee_included=review.included_eur,
        reviewed_net_staking_income=net,
        earn_inflows=sum(item.inflows for item in asset_rows),
        disposals=sum(
            1
            for item in _list(db, DisposalCalculation)
            if item.tax_calculation_run_id == run.id
        ),
        allocations=sum(
            1
            for item in _list(db, LotAllocation)
            if item.tax_calculation_run_id == run.id
        ),
        asset_rows=asset_rows,
        inventory_rows=inventory_rows,
        review=review,
    )


@router.post("/exports", response_model=ExportResponse)
def create_export(data: ExportInput, db: Db) -> dict[str, Any]:
    run = db.get(TaxCalculationRun, data.tax_calculation_run_id)
    if run is None:
        raise _not_found("tax_calculation_not_found")
    format_version = EXPORT_FORMAT_VERSIONS[data.kind]
    existing_run = next(
        (
            item
            for item in _list(db, ExportRun)
            if item.tax_calculation_run_id == run.id
            and item.kind == data.kind
            and item.format_version == format_version
        ),
        None,
    )
    if existing_run:
        artifact = next(
            item
            for item in _list(db, ExportArtifact)
            if item.export_run_id == existing_run.id
        )
        return _export_response(existing_run, artifact, duplicate=True)
    now = utc_now()
    export_run = ExportRun(
        tax_calculation_run_id=run.id,
        kind=data.kind,
        status=ExportStatus.CREATED,
        period_start=run.period_start,
        period_end=run.period_end,
        rules_fingerprint=run.rules_fingerprint,
        format_version=format_version,
        started_at=now,
    )
    extension, media_type = export_extension(data.kind)
    file_name = (
        f"kraken-tax-report-{run.period_start.year}-{uuid4().hex}.pdf"
        if data.kind == ExportKind.TAX_REPORT_PDF
        else f"tax-export-{uuid4().hex}.{extension}"
    )
    target = safe_export_path(Path(get_settings().export_directory), file_name)
    target.parent.mkdir(parents=True, exist_ok=True)
    if data.kind == ExportKind.TAX_REPORT_PDF:
        content = tax_report_pdf(_tax_report_data(db, run, now))
    else:
        rows = _export_rows(data.kind, db, run)
        content = csv_bytes(EXPORT_COLUMNS[data.kind], rows)
    target.write_bytes(content)
    artifact = ExportArtifact(
        export_run_id=export_run.id,
        kind=data.kind,
        file_name=file_name,
        media_type=media_type,
        size_bytes=len(content),
        sha256_hash=sha256(content).hexdigest(),
        created_at=now,
    )
    export_run.status = ExportStatus.COMPLETED
    export_run.completed_at = now
    db.add_all([export_run, artifact])
    db.add(
        AuditEvent(
            occurred_at=now,
            event_type="tax.export_completed",
            entity_type="ExportRun",
            entity_id=export_run.id,
            actor_type=AuditActorType.SYSTEM,
            actor_id="tax-export",
            metadata={
                "kind": data.kind.value,
                "format_version": format_version,
                "artifact_id": str(artifact.id),
                "sha256": artifact.sha256_hash,
                "size_bytes": artifact.size_bytes,
            },
        )
    )
    db.commit()
    return _export_response(export_run, artifact, duplicate=False)


def _export_rows(
    kind: ExportKind, db: Session, run: TaxCalculationRun
) -> list[dict[str, object]]:
    export_limit = 1_000_000
    if kind == ExportKind.TAX_JOURNAL_CSV:
        page = journal_list(db, 0, export_limit, run.period_start.year, run_id=run.id)
        return _dict_list(page.get("items"))
    if kind == ExportKind.FIFO_ALLOCATIONS_CSV:
        page = allocation_list(
            db, 0, export_limit, run.period_start.year, run_id=run.id
        )
        return _dict_list(page.get("items"))
    if kind == ExportKind.INVENTORY_CSV:
        page = inventory_lot_list(
            db, 0, export_limit, run.period_start.year, run_id=run.id
        )
        return _dict_list(page.get("items"))
    if kind == ExportKind.VALUATION_EVIDENCE_CSV:
        rows = [
            {
                "id": str(item.id),
                "asset": item.asset_code,
                "date": item.price_date,
                "method": item.method.value,
                "method_version": item.method_version,
                "unit_price_eur": item.unit_price_eur,
                "quantity": item.quantity,
                "eur_value": item.eur_value,
                "gross_quantity": item.gross_quantity,
                "fee_quantity": item.fee_quantity,
                "net_quantity": item.net_quantity,
                "gross_income_eur": item.gross_income_eur,
                "fee_value_eur": item.fee_value_eur,
                "net_acquisition_value_eur": item.net_acquisition_value_eur,
                "valuation_basis": item.valuation_basis,
                "fee_tax_classification": (
                    item.fee_tax_classification.value
                    if item.fee_tax_classification
                    else None
                ),
                "fee_tax_review_status": (
                    item.fee_tax_review_status.value
                    if item.fee_tax_review_status
                    else None
                ),
                "provider": item.provider,
                "version": item.version,
            }
            for item in _list(db, ValuationDecision)
            if run.period_start <= item.price_date <= run.period_end
        ]
        rows.sort(key=lambda row: (str(row["date"]), str(row["id"])))
        return _dict_list(rows)
    if kind == ExportKind.REVIEWS_CSV:
        effective = _effective_review_decisions(db)
        cases_by_valuation: dict[UUID, TaxReviewCase] = {}
        for case in _list(db, TaxReviewCase):
            if (
                case.code == "tax_staking_platform_fee_candidate_review"
                and case.source_object_type == "ValuationDecision"
            ):
                previous_case = cases_by_valuation.get(case.source_object_id)
                if previous_case is None or (case.occurred_at, case.id.hex) > (
                    previous_case.occurred_at,
                    previous_case.id.hex,
                ):
                    cases_by_valuation[case.source_object_id] = case
        rows = [
            {
                "id": str(case.id) if case else None,
                "code": (
                    case.code if case else "tax_staking_platform_fee_candidate_review"
                ),
                "message": case.message if case else None,
                "source_object_type": "ValuationDecision",
                "source_object_id": str(valuation.id),
                "occurred_at": valuation.valuation_at,
                "valuation_decision_id": str(valuation.id),
                "fee_value_eur": valuation.fee_value_eur,
                "review_status": "resolved" if current else "open",
                "decision": current.decision.value if current else None,
                "reason": current.reason if current else None,
                "actor_id": current.actor_id if current else None,
                "decided_at": current.decided_at if current else None,
                "version": current.version if current else None,
                "batch_id": current.batch_id if current else None,
            }
            for valuation in _latest_decisions(db).values()
            if run.period_start <= valuation.price_date <= run.period_end
            and valuation.fee_tax_classification
            is FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
            for case in [cases_by_valuation.get(valuation.id)]
            for current in [effective.get(valuation.id)]
        ]
        rows.sort(key=lambda row: (str(row["occurred_at"]), str(row["id"])))
        return _dict_list(rows)
    return _dict_list([tax_summary(db, run.period_start.year)])


def _export_response(
    run: ExportRun, artifact: ExportArtifact, *, duplicate: bool
) -> dict[str, Any]:
    return {
        "id": str(run.id),
        "status": run.status.value,
        "kind": run.kind.value,
        "format_version": run.format_version,
        "artifact_id": str(artifact.id),
        "download_url": f"/api/exports/{artifact.id}/download",
        "duplicate": duplicate,
    }


@router.get("/exports", response_model=ExportPage)
def export_list(
    db: Db, offset: Offset = 0, limit: Limit = 100, year: int | None = None
) -> dict[str, Any]:
    runs = {run.id: run for run in _list(db, ExportRun)}
    rows = [
        {
            "id": str(item.id),
            "export_run_id": str(item.export_run_id),
            "kind": item.kind.value,
            "format_version": runs[item.export_run_id].format_version,
            "status": runs[item.export_run_id].status.value,
            "file_name": item.file_name,
            "media_type": item.media_type,
            "size_bytes": item.size_bytes,
            "created_at": item.created_at,
            "download_url": f"/api/exports/{item.id}/download",
        }
        for item in _list(db, ExportArtifact)
        if year is None or runs[item.export_run_id].period_start.year == year
    ]
    rows.sort(key=lambda row: (str(row["created_at"]), str(row["id"])), reverse=True)
    return _page(rows, offset, limit)


@router.get("/exports/{item_id}", response_model=TaxDetailResponse)
def export_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    item = db.get(ExportArtifact, item_id)
    if item is None:
        raise _not_found("export_not_found")
    run = db.get(ExportRun, item.export_run_id)
    return {
        "id": str(item.id),
        "export_run_id": str(item.export_run_id),
        "kind": item.kind.value,
        "format_version": run.format_version if run else "unknown",
        "status": run.status.value if run else "unknown",
        "file_name": item.file_name,
        "media_type": item.media_type,
        "size_bytes": item.size_bytes,
        "sha256": item.sha256_hash,
        "created_at": item.created_at,
        "download_url": f"/api/exports/{item.id}/download",
    }


@router.get("/exports/{item_id}/download", response_class=FileResponse)
def download_export(item_id: UUID, db: Db) -> FileResponse:
    item = db.get(ExportArtifact, item_id)
    if item is None:
        raise _not_found("export_not_found")
    target = safe_export_path(Path(get_settings().export_directory), item.file_name)
    if not target.is_file():
        raise _not_found("export_file_not_found")
    return FileResponse(target, media_type=item.media_type, filename=item.file_name)
