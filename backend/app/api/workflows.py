import csv
import io
from collections.abc import Callable
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Annotated, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_serializer
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.kraken.service import KrakenCsvImportService
from app.adapters.kraken.transformation import (
    TRANSFORMATION_CONTRACT_VERSION,
    KrakenTransformationService,
)
from app.config.settings import get_settings
from app.core.entities import AuditActorType, AuditEvent
from app.core.identifiers import Uuid4IdGenerator
from app.core.tax import TaxReviewCase
from app.core.time import utc_now
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    DecisionType,
    DisposalEvent,
    FeeEvent,
    TradeExecution,
    TransformationDecision,
    TransformationRun,
    TransformationRunSession,
    TransformationStatus,
    ValuationMethod,
    ValuationRequirement,
)
from app.core.valuation import (
    METHOD_VERSION,
    DailyPrice,
    FeeTaxClassification,
    FeeTaxReviewStatus,
    PriceMethod,
    PriceProviderError,
    ProviderEvidence,
    RewardValuationComponents,
    RewardValuationError,
    ValuationDecision,
    ValuationDecisionStatus,
    ValuationRun,
    ValuationRunStatus,
    calculate_eur_value,
    calculate_reward_valuation,
    daily_average,
    evidence_hash,
    exact_decimal_sum,
    transition_valuation_run,
)
from app.database.dashboard_queries import SqlAlchemyDashboardQueries
from app.database.session import get_session
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.service import ImportService
from app.imports.validation import RequiredFieldsValidator
from app.infrastructure.coingecko import MAPPING_VERSION, CoinGeckoProvider
from app.services.valuation_fetch import plan_fetches, prefetch

router = APIRouter(prefix="/api", tags=["workflows"])
Db = Annotated[Session, Depends(get_session)]
Offset = Annotated[int, Query(ge=0)]
Limit = Annotated[int, Query(ge=1, le=200)]
VALUABLE_DOMAIN_MODELS: dict[str, type[Any]] = {
    "AcquisitionLot": AcquisitionLot,
    "DisposalEvent": DisposalEvent,
    "FeeEvent": FeeEvent,
    # Compatibility with early Sprint-3A development databases.
    "acquisition_lot": AcquisitionLot,
    "disposal_event": DisposalEvent,
    "fee_event": FeeEvent,
}


def request_unit_of_work_factory(
    db: Session,
) -> Callable[[], SqlAlchemyUnitOfWork]:
    request_sessions: sessionmaker[Session] = sessionmaker(
        bind=db.get_bind(), autoflush=False, expire_on_commit=False
    )

    def create() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(request_sessions)

    return create


class ManualPriceInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    asset: str = Field(
        min_length=1, max_length=32, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"
    )
    date: date
    price_eur: Decimal
    source: str = Field(min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1024)
    entered_by: str = Field(default="local-user", min_length=1)
    external_reference: str | None = None


class DecimalModel(BaseModel):
    @field_serializer("*", when_used="json", check_fields=False)
    def serialize_decimal(self, value: Any) -> Any:
        return str(value) if isinstance(value, Decimal) else value


class PageResponse(DecimalModel):
    items: list[dict[str, Any]]
    total: int
    offset: int
    limit: int


class DetailResponse(DecimalModel):
    model_config = ConfigDict(extra="allow")
    id: str


class DashboardResponse(DecimalModel):
    imports: int
    raw_records: int
    transformation_runs: int
    rewards: int
    trades: int
    acquisitions: int
    disposals: int
    open_valuations: int
    resolved_valuations: int
    review_cases: int
    last_import_at: Any | None
    last_valuation_at: Any | None
    price_source: dict[str, Any]


class ImportTransformationSummary(DecimalModel):
    run_id: str
    status: str
    checked: int
    review_cases: int
    requirements: int
    contract_version: str
    created_objects: int
    reused_objects: int


class ImportResultResponse(DecimalModel):
    model_config = ConfigDict(extra="allow")
    session_id: str | None = None
    status: str
    duplicate: bool = False
    accepted: int = 0
    errors: list[dict[str, Any]]
    transformation: ImportTransformationSummary | None = None


class TransformationResultResponse(DecimalModel):
    id: str
    status: str
    checked: int
    review_cases: int
    valuation_requirements: int
    contract_version: str
    created_objects: int
    reused_objects: int


class ValuationRunResponse(DecimalModel):
    id: str
    status: str
    method_version: str
    checked: int
    resolved: int
    reviews: int
    gross_income_total_eur: str
    fee_candidate_total_eur: str
    net_acquisition_total_eur: str


class ManualPriceResponse(DecimalModel):
    id: str
    version: int
    status: str
    duplicate: bool


class ManualCsvResponse(DecimalModel):
    count: int
    items: list[ManualPriceResponse]


class SystemStatusResponse(DecimalModel):
    backend: bool
    database: bool
    migration: str
    coingecko_mode: str
    api_key_configured: bool
    method_version: str
    asset_mapping_version: str


def page(items: list[dict[str, Any]], offset: int, limit: int) -> dict[str, Any]:
    return {
        "items": items[offset : offset + limit],
        "total": len(items),
        "offset": offset,
        "limit": limit,
    }


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Db) -> dict[str, Any]:
    query = SqlAlchemyDashboardQueries(db)
    counts = query.counts()
    latest_import = query.latest_import()
    latest_run = query.latest_valuation_run()
    settings = get_settings()
    return {
        "imports": counts.imports,
        "raw_records": counts.raw_records,
        "transformation_runs": counts.transformation_runs,
        "rewards": counts.acquisitions,
        "trades": counts.trades,
        "acquisitions": counts.acquisitions,
        "disposals": counts.disposals,
        "open_valuations": max(
            0, counts.valuation_requirements - counts.valuation_decisions
        ),
        "resolved_valuations": counts.resolved_valuation_decisions,
        "review_cases": counts.review_cases,
        "last_import_at": latest_import.started_at if latest_import else None,
        "last_valuation_at": latest_run.started_at if latest_run else None,
        "price_source": {
            "mode": settings.coingecko_api_mode,
            "available": settings.coingecko_api_mode != "disabled",
        },
    }


@router.post("/imports/kraken", response_model=ImportResultResponse)
async def import_kraken(
    file: Annotated[UploadFile, File()], db: Db, transform: bool = False
) -> dict[str, Any]:
    settings = get_settings()
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            422,
            detail={
                "code": "import_invalid_file_type",
                "message": "Nur CSV-Dateien sind zulässig.",
            },
        )
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            413,
            detail={
                "code": "import_file_too_large",
                "message": "Die Datei überschreitet das Uploadlimit.",
            },
        )
    unit_of_work_factory = request_unit_of_work_factory(db)
    service = KrakenCsvImportService(
        import_service=ImportService(
            unit_of_work_factory=unit_of_work_factory,
            id_generator=Uuid4IdGenerator(),
            validator=RequiredFieldsValidator(),
        ),
        id_generator=Uuid4IdGenerator(),
    )
    result = service.import_csv(
        raw_data=content,
        actor_type=AuditActorType.USER,
        actor_id="local-user",
        source_name=file.filename,
    )
    if result.import_result is None:
        raise HTTPException(
            422,
            detail={
                "code": "import_validation_failed",
                "message": "Die Kraken-CSV ist ungültig.",
                "errors": [vars(error) for error in result.errors],
            },
        )
    imported = result.import_result
    response: dict[str, Any] = {
        "session_id": str(imported.session_id),
        "status": imported.outcome.value,
        "duplicate": imported.skipped,
        "accepted": imported.accepted_count,
        "errors": [vars(error) for error in imported.errors],
    }
    if transform and imported.outcome.value != "failed":
        canonical_session_id = imported.duplicate_of_session_id or imported.session_id
        existing = reusable_transformation_run(
            db, canonical_session_id, TRANSFORMATION_CONTRACT_VERSION
        )
        if existing is not None:
            requirements = sum(
                item.transformation_run_id == existing.id
                for item in list_entities(db, ValuationRequirement)
            )
            response["transformation"] = {
                "run_id": str(existing.id),
                "status": existing.status.value,
                "checked": existing.checked_records,
                "review_cases": existing.review_cases,
                "requirements": requirements,
                "contract_version": existing.contract_version,
                "created_objects": existing.created_objects,
                "reused_objects": sum(
                    decision.transformation_run_id == existing.id
                    and decision.decision_type is DecisionType.DOMAIN_EVENT_REUSED
                    for decision in list_entities(db, TransformationDecision)
                ),
            }
        else:
            transformed = KrakenTransformationService(
                unit_of_work_factory=unit_of_work_factory
            ).transform(
                import_session_ids=[canonical_session_id], actor_id="local-user"
            )
            response["transformation"] = {
                "run_id": str(transformed.run_id),
                "status": transformed.status.value,
                "checked": transformed.checked_records,
                "review_cases": transformed.review_cases,
                "requirements": transformed.valuation_requirements,
                "contract_version": TRANSFORMATION_CONTRACT_VERSION,
                "created_objects": transformed.acquisitions
                + transformed.disposals
                + transformed.trade_executions
                + transformed.fee_events
                + transformed.valuation_requirements,
                "reused_objects": transformed.reused_objects,
            }
    return response


@router.post("/transformations", response_model=TransformationResultResponse)
def transform(session_ids: list[UUID], db: Db) -> dict[str, Any]:
    result = KrakenTransformationService(
        unit_of_work_factory=request_unit_of_work_factory(db)
    ).transform(import_session_ids=session_ids, actor_id="local-user")
    return {
        "id": str(result.run_id),
        "status": result.status.value,
        "checked": result.checked_records,
        "review_cases": result.review_cases,
        "valuation_requirements": result.valuation_requirements,
        "contract_version": TRANSFORMATION_CONTRACT_VERSION,
        "created_objects": result.acquisitions
        + result.disposals
        + result.trade_executions
        + result.fee_events
        + result.valuation_requirements,
        "reused_objects": result.reused_objects,
    }


def list_entities(db: Session, model: type[Any]) -> list[Any]:
    return list(db.scalars(select(model)))


def reusable_transformation_run(
    db: Session, import_session_id: UUID, contract_version: str
) -> TransformationRun | None:
    linked_run_ids = {
        item.transformation_run_id
        for item in list_entities(db, TransformationRunSession)
        if item.import_session_id == import_session_id
    }
    successful = [
        run
        for run in list_entities(db, TransformationRun)
        if run.id in linked_run_ids
        and run.contract_version == contract_version
        and run.status
        in {
            TransformationStatus.COMPLETED,
            TransformationStatus.COMPLETED_WITH_REVIEW,
        }
    ]
    return max(successful, key=lambda item: item.started_at, default=None)


def not_found(code: str) -> HTTPException:
    return HTTPException(
        404, detail={"code": code, "message": "Der Datensatz wurde nicht gefunden."}
    )


@router.get("/imports", response_model=PageResponse)
def imports(
    db: Db, offset: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)
) -> dict[str, Any]:
    from app.core.entities import ImportSession

    values = [
        {
            "id": str(x.id),
            "source": x.source,
            "status": x.status.value,
            "records": x.persisted_count,
            "hash": x.import_hash,
            "imported_at": x.started_at,
            "error": x.error_summary,
        }
        for x in list_entities(db, ImportSession)
    ]
    return page(values, offset, limit)


@router.get("/imports/{item_id}", response_model=DetailResponse)
def import_detail(item_id: UUID, db: Db, include_raw: bool = False) -> dict[str, Any]:
    from app.core.entities import ImportSession, RawImportRecord
    from app.core.transformation import TransformationRunSession

    item = db.get(ImportSession, item_id)
    if item is None:
        raise not_found("import_not_found")
    raw = [
        x for x in list_entities(db, RawImportRecord) if x.import_session_id == item.id
    ]
    links = [
        x
        for x in list_entities(db, TransformationRunSession)
        if x.import_session_id == item.id
    ]
    return {
        "id": str(item.id),
        "source": item.source,
        "filename": item.source,
        "status": item.status.value,
        "hash": item.import_hash,
        "imported_at": item.started_at,
        "records": [
            {
                "id": str(x.id),
                "sequence": x.sequence_number,
                "external_id": x.external_id,
                "payload": x.payload if include_raw else None,
            }
            for x in raw[:200]
        ],
        "transformation_run_ids": [str(x.transformation_run_id) for x in links],
    }


@router.get("/transformations", response_model=PageResponse)
def transformations(db: Db, offset: Offset = 0, limit: Limit = 100) -> dict[str, Any]:
    from app.core.transformation import TransformationRun

    rows = [
        {
            "id": str(x.id),
            "status": x.status.value,
            "started_at": x.started_at,
            "completed_at": x.completed_at,
            "checked": x.checked_records,
            "review_cases": x.review_cases,
        }
        for x in list_entities(db, TransformationRun)
    ]
    return page(rows, offset, limit)


@router.get("/transformations/{item_id}", response_model=DetailResponse)
def transformation_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    from app.core.transformation import TransformationDecision, TransformationRun

    item = db.get(TransformationRun, item_id)
    if item is None:
        raise not_found("transformation_not_found")
    decisions = [
        x
        for x in list_entities(db, TransformationDecision)
        if x.transformation_run_id == item.id
    ]
    return {
        "id": str(item.id),
        "status": item.status.value,
        "contract_version": item.contract_version,
        "decisions": [
            {
                "id": str(x.id),
                "reason_code": x.reason_code,
                "type": x.decision_type.value,
                "raw_import_record_id": str(x.raw_import_record_id),
            }
            for x in decisions
        ],
    }


@router.get("/events", response_model=PageResponse)
def events(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    event_type: Literal["acquisition", "disposal", "trade", "fee"] | None = None,
) -> dict[str, Any]:
    values: list[dict[str, Any]] = []
    for kind, model in (
        ("Erwerb", AcquisitionLot),
        ("Veräußerung", DisposalEvent),
        ("Trade", TradeExecution),
        ("Gebühr", FeeEvent),
    ):
        if (
            event_type
            and event_type
            != {
                "Erwerb": "acquisition",
                "Veräußerung": "disposal",
                "Trade": "trade",
                "Gebühr": "fee",
            }[kind]
        ):
            continue
        for item in list_entities(db, model):
            values.append(
                {
                    "id": str(item.id),
                    "type": kind,
                    "asset": getattr(
                        item, "asset_code", getattr(item, "base_asset", "")
                    ),
                    "quantity": str(
                        getattr(item, "quantity", getattr(item, "volume", ""))
                    ),
                    "occurred_at": item.occurred_at,
                    "valuation_status": getattr(item, "valuation_status", None),
                }
            )
    values.sort(key=lambda x: str(x["occurred_at"]), reverse=True)
    return page(values, offset, limit)


@router.get("/events/{event_type}/{item_id}", response_model=DetailResponse)
def event_detail(
    event_type: str, item_id: UUID, db: Db, include_raw: bool = False
) -> dict[str, Any]:
    from app.core.entities import AuditEvent, ImportSession, RawImportRecord
    from app.core.transformation import DomainProvenance, TransformationRun

    models = {
        "acquisition": AcquisitionLot,
        "disposal": DisposalEvent,
        "trade": TradeExecution,
        "fee": FeeEvent,
    }
    model = models.get(event_type)
    item = db.get(model, item_id) if model else None
    if item is None:
        raise not_found("event_not_found")
    provenance = [
        x for x in list_entities(db, DomainProvenance) if x.domain_object_id == item_id
    ]
    raw_by_id = {
        raw.id: raw
        for raw in list_entities(db, RawImportRecord)
        if raw.id in {link.raw_import_record_id for link in provenance}
    }
    imports_by_id = {
        imported.id: imported
        for imported in list_entities(db, ImportSession)
        if imported.id in {link.import_session_id for link in provenance}
    }
    runs_by_id = {
        run.id: run
        for run in list_entities(db, TransformationRun)
        if run.id in {link.transformation_run_id for link in provenance}
    }
    audits = [
        audit for audit in list_entities(db, AuditEvent) if audit.entity_id == item_id
    ]
    return {
        "id": str(item.id),
        "type": event_type,
        "asset": getattr(item, "asset_code", getattr(item, "base_asset", "")),
        "quantity": str(getattr(item, "quantity", getattr(item, "volume", ""))),
        "occurred_at": item.occurred_at,
        "provenance": [
            {
                "raw_import_record_id": str(x.raw_import_record_id),
                "import_session_id": str(x.import_session_id),
                "transformation_run_id": str(x.transformation_run_id),
                "raw_record": (
                    {
                        "id": str(raw_by_id[x.raw_import_record_id].id),
                        "external_id": raw_by_id[x.raw_import_record_id].external_id,
                        "payload": (
                            raw_by_id[x.raw_import_record_id].payload
                            if include_raw
                            else None
                        ),
                    }
                    if x.raw_import_record_id in raw_by_id
                    else None
                ),
                "import_session": (
                    {
                        "id": str(imports_by_id[x.import_session_id].id),
                        "source": imports_by_id[x.import_session_id].source,
                        "status": imports_by_id[x.import_session_id].status.value,
                    }
                    if x.import_session_id in imports_by_id
                    else None
                ),
                "transformation_run": (
                    {
                        "id": str(runs_by_id[x.transformation_run_id].id),
                        "status": runs_by_id[x.transformation_run_id].status.value,
                        "contract_version": (
                            runs_by_id[x.transformation_run_id].contract_version
                        ),
                    }
                    if x.transformation_run_id in runs_by_id
                    else None
                ),
            }
            for x in provenance
        ],
        "audit": [
            {
                "event_type": audit.event_type,
                "occurred_at": audit.occurred_at,
                "metadata": audit.metadata,
            }
            for audit in audits
        ],
    }


@router.get("/valuation-requirements", response_model=PageResponse)
def requirements(
    db: Db,
    status: Literal["pending", "resolved"] | None = None,
    asset: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]+$")] = None,
    offset: Offset = 0,
    limit: Limit = 100,
) -> dict[str, Any]:
    values = list_entities(db, ValuationRequirement)
    if asset:
        values = [item for item in values if item.asset_code == asset.upper()]
    decided = {
        item.valuation_requirement_id for item in list_entities(db, ValuationDecision)
    }
    rows = [
        {
            "id": str(x.id),
            "asset": x.asset_code,
            "date": x.valuation_at,
            "method": x.method.value,
            "status": "resolved" if x.id in decided else "pending",
            "event_type": x.domain_object_type,
        }
        for x in values
    ]
    if status:
        rows = [row for row in rows if row["status"] == status]
    return page(rows, offset, limit)


@router.post("/prices/manual", response_model=ManualPriceResponse)
def manual_price(data: ManualPriceInput, db: Db) -> dict[str, Any]:
    result = _add_manual_price(data, db)
    db.commit()
    return result


def _add_manual_price(data: ManualPriceInput, db: Session) -> dict[str, Any]:
    if data.date > utc_now().date() or data.price_eur <= 0:
        raise HTTPException(
            422,
            detail={
                "code": "valuation_invalid_price",
                "message": "Datum und Kurs sind ungültig.",
            },
        )
    previous = [
        item
        for item in list_entities(db, DailyPrice)
        if item.asset_code == data.asset.upper()
        and item.price_date == data.date
        and item.method == PriceMethod.MANUAL_DAILY_PRICE
    ]
    previous.sort(key=lambda item: item.version, reverse=True)
    if previous and (
        previous[0].unit_price_eur == data.price_eur
        and previous[0].source == data.source
        and previous[0].reason == data.reason
        and previous[0].external_reference == data.external_reference
    ):
        db.add(
            AuditEvent(
                occurred_at=utc_now(),
                event_type="valuation.duplicate_detected",
                entity_type="daily_price",
                entity_id=previous[0].id,
                actor_type=AuditActorType.USER,
                actor_id=data.entered_by,
                metadata={"kind": "manual_daily_price"},
            )
        )
        return {
            "id": str(previous[0].id),
            "version": previous[0].version,
            "status": previous[0].status.value,
            "duplicate": True,
        }
    canonical = "|".join(
        (
            data.asset.upper(),
            data.date.isoformat(),
            str(data.price_eur),
            data.source,
            data.reason,
            data.external_reference or "",
        )
    )
    price = DailyPrice(
        asset_code=data.asset,
        price_date=data.date,
        unit_price_eur=data.price_eur,
        method=PriceMethod.MANUAL_DAILY_PRICE,
        source=data.source,
        provider="manual",
        provider_contract_version="manual-v1",
        evidence_hash=sha256(canonical.encode("utf-8")).hexdigest(),
        sample_count=1,
        fetched_at=utc_now(),
        status=ValuationDecisionStatus.RESOLVED,
        version=(previous[0].version + 1 if previous else 1),
        external_reference=data.external_reference,
        reason=data.reason,
        entered_by=data.entered_by,
        supersedes_id=(previous[0].id if previous else None),
    )
    db.add(price)
    db.add(
        AuditEvent(
            occurred_at=utc_now(),
            event_type=(
                "valuation.manual_price_corrected"
                if previous
                else "valuation.manual_price_created"
            ),
            entity_type="daily_price",
            entity_id=price.id,
            actor_type=AuditActorType.USER,
            actor_id=data.entered_by,
            metadata={
                "asset": price.asset_code,
                "date": str(price.price_date),
                "version": price.version,
            },
        )
    )
    return {
        "id": str(price.id),
        "version": price.version,
        "status": price.status.value,
        "duplicate": False,
    }


@router.post("/prices/manual/csv", response_model=ManualCsvResponse)
async def manual_csv(file: Annotated[UploadFile, File()], db: Db) -> dict[str, Any]:
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(
            422,
            detail={
                "code": "manual_csv_invalid_file_type",
                "message": "Nur CSV-Dateien sind zulässig.",
            },
        )
    raw = await file.read(512_001)
    if len(raw) > 512_000:
        raise HTTPException(413, detail={"code": "manual_csv_too_large"})
    try:
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
        expected = {"asset", "date", "price_eur", "source", "reason"}
        if set(rows[0] if rows else {}) != expected:
            raise ValueError("header")
        parsed: list[ManualPriceInput] = []
        for line_number, row in enumerate(rows, start=2):
            try:
                item = ManualPriceInput(
                    asset=row["asset"],
                    date=date.fromisoformat(row["date"]),
                    price_eur=Decimal(row["price_eur"]),
                    source=row["source"],
                    reason=row["reason"],
                )
                if item.price_eur <= 0:
                    raise HTTPException(
                        422,
                        detail={
                            "code": "valuation_invalid_price",
                            "message": "Der Kurs muss positiv sein.",
                            "line": line_number,
                            "field": "price_eur",
                        },
                    )
                if item.date > utc_now().date():
                    raise HTTPException(
                        422,
                        detail={
                            "code": "valuation_future_date",
                            "message": "Zukünftige Tage sind nicht zulässig.",
                            "line": line_number,
                            "field": "date",
                        },
                    )
                parsed.append(item)
            except (
                InvalidOperation,
                KeyError,
                ValueError,
                ValidationError,
            ) as error:
                field = (
                    str(error.errors()[0]["loc"][-1])
                    if isinstance(error, ValidationError)
                    else "row"
                )
                raise HTTPException(
                    422,
                    detail={
                        "code": "manual_csv_invalid",
                        "message": "Die Kurs-CSV ist ungültig.",
                        "line": line_number,
                        "field": field,
                    },
                ) from error
    except (
        csv.Error,
        UnicodeDecodeError,
        ValueError,
        InvalidOperation,
        KeyError,
    ) as error:
        raise HTTPException(
            422,
            detail={
                "code": "manual_csv_invalid",
                "message": "Die Kurs-CSV ist ungültig.",
            },
        ) from error
    results = [_add_manual_price(item, db) for item in parsed]
    db.commit()
    return {"count": len(results), "items": results}


@router.get("/prices", response_model=PageResponse)
def prices(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    asset: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]+$")] = None,
    method: PriceMethod | None = None,
) -> dict[str, Any]:
    all_prices = list_entities(db, DailyPrice)
    superseded_ids = {x.supersedes_id for x in all_prices if x.supersedes_id}
    rows = [
        {
            "id": str(x.id),
            "asset": x.asset_code,
            "date": x.price_date,
            "price_eur": str(x.unit_price_eur),
            "method": x.method.value,
            "source": x.source,
            "status": x.status.value,
            "effective_status": (
                ValuationDecisionStatus.SUPERSEDED.value
                if x.id in superseded_ids
                else x.status.value
            ),
            "samples": x.sample_count,
            "version": x.version,
            "fetched_at": x.fetched_at,
        }
        for x in all_prices
        if (asset is None or x.asset_code == asset.upper())
        and (method is None or x.method == method)
    ]
    return page(rows, offset, limit)


@router.get("/prices/{item_id}", response_model=DetailResponse)
def price_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    from app.core.entities import AuditEvent

    item = db.get(DailyPrice, item_id)
    if item is None:
        raise not_found("price_not_found")
    evidence = (
        db.get(ProviderEvidence, item.provider_evidence_id)
        if item.provider_evidence_id
        else None
    )
    successor = next(
        (
            price
            for price in list_entities(db, DailyPrice)
            if price.supersedes_id == item.id
        ),
        None,
    )
    audits = [
        event
        for event in list_entities(db, AuditEvent)
        if event.entity_id in {item.id, successor.id if successor else item.id}
    ]
    return {
        "id": str(item.id),
        "asset": item.asset_code,
        "date": item.price_date,
        "price_eur": str(item.unit_price_eur),
        "method": item.method.value,
        "source": item.source,
        "version": item.version,
        "effective_status": (
            ValuationDecisionStatus.SUPERSEDED.value if successor else item.status.value
        ),
        "supersedes_id": str(item.supersedes_id) if item.supersedes_id else None,
        "superseded_by_id": str(successor.id) if successor else None,
        "reason": item.reason,
        "provider_evidence": (
            {
                "id": str(evidence.id),
                "provider": evidence.provider,
                "asset_id": evidence.provider_asset_id,
                "requested_from": evidence.requested_from,
                "requested_to": evidence.requested_to,
                "response_hash": evidence.response_hash,
                "observation_count": evidence.observation_count,
            }
            if evidence
            else None
        ),
        "audit": [
            {
                "event_type": event.event_type,
                "occurred_at": event.occurred_at,
                "metadata": event.metadata,
            }
            for event in audits
        ],
    }


@router.post("/valuations", response_model=ValuationRunResponse)
def valuations(
    db: Db,
    method_version: Annotated[str, Query(min_length=1, max_length=64)] = METHOD_VERSION,
    refresh_prices: bool = False,
) -> dict[str, Any]:
    settings, now = get_settings(), utc_now()
    gross_income_values: list[Decimal] = []
    fee_candidate_values: list[Decimal] = []
    net_acquisition_values: list[Decimal] = []
    run = ValuationRun(
        provider="coingecko",
        correlation_id=uuid4(),
        started_at=now,
        status=ValuationRunStatus.CREATED,
        method_version=method_version,
    )
    db.add(run)
    db.flush()

    def audit(event_type: str, metadata: dict[str, Any]) -> None:
        db.add(
            AuditEvent(
                occurred_at=utc_now(),
                event_type=event_type,
                entity_type="valuation_run",
                entity_id=run.id,
                actor_type=AuditActorType.SYSTEM,
                actor_id="valuation-engine",
                metadata=metadata,
            )
        )

    audit("valuation.run_created", {"method_version": run.method_version})
    transition_valuation_run(run, ValuationRunStatus.FETCHING, now)
    all_decisions = list_entities(db, ValuationDecision)
    existing_review_audits = [
        event
        for event in list_entities(db, AuditEvent)
        if event.event_type == "valuation.review_created"
        and "decision_id" not in event.metadata
    ]
    reviewed_requirement_versions = {
        (
            str(event.metadata.get("requirement_id", "")),
            str(event.metadata.get("method_version", "")),
        )
        for event in existing_review_audits
    }
    decisions_by_requirement: dict[UUID, list[ValuationDecision]] = {}
    for existing_decision in all_decisions:
        decisions_by_requirement.setdefault(
            existing_decision.valuation_requirement_id, []
        ).append(existing_decision)
    pending: list[ValuationRequirement] = []
    for requirement in list_entities(db, ValuationRequirement):
        prior = decisions_by_requirement.get(requirement.id, [])
        if (
            any(item.method_version == method_version for item in prior)
            or (str(requirement.id), method_version) in reviewed_requirement_versions
        ):
            audit(
                "valuation.duplicate_detected",
                {
                    "requirement_id": str(requirement.id),
                    "method_version": method_version,
                },
            )
        else:
            pending.append(requirement)
    run.checked_requirements = len(pending)
    provider = CoinGeckoProvider(
        base_url=settings.coingecko_base_url,
        mode=settings.coingecko_api_mode,
        api_key=settings.coingecko_api_key,
        timeout_seconds=settings.coingecko_timeout_seconds,
        min_interval_seconds=settings.coingecko_min_interval_seconds,
        rate_limit_retry_base_seconds=settings.coingecko_rate_limit_retry_base_seconds,
    )
    fetchable: list[ValuationRequirement] = []
    for requirement in pending:
        model = VALUABLE_DOMAIN_MODELS.get(requirement.domain_object_type)
        domain_item = db.get(model, requirement.domain_object_id) if model else None
        if domain_item is None:
            continue
        if isinstance(domain_item, AcquisitionLot) and domain_item.acquisition_type in {
            AcquisitionType.STAKING_REWARD,
            AcquisitionType.LEGACY_STAKING_REWARD,
        }:
            try:
                calculate_reward_valuation(
                    net_quantity=domain_item.quantity,
                    gross_quantity=domain_item.gross_quantity,
                    fee_quantity=domain_item.fee_quantity,
                    asset_code=domain_item.asset_code,
                    fee_asset=domain_item.fee_asset,
                    unit_price_eur=Decimal("1"),
                    method_version=method_version,
                )
            except RewardValuationError:
                continue
        fetchable.append(requirement)
    plans = plan_fetches(
        fetchable,
        list_entities(db, DailyPrice),
        provider,
        refresh_prices=refresh_prices,
    )
    batches = prefetch(plans, provider, db, audit=audit)
    cache: dict[tuple[str, date], DailyPrice] = {
        (price.asset_code, price.price_date): price
        for plan in plans
        for price in plan.existing_prices
    }
    transition_valuation_run(run, ValuationRunStatus.APPLYING, now)
    for requirement in pending:
        model = VALUABLE_DOMAIN_MODELS.get(requirement.domain_object_type)
        item = db.get(model, requirement.domain_object_id) if model else None
        if item is None:
            run.review_count += 1
            audit(
                "valuation.review_created",
                {
                    "requirement_id": str(requirement.id),
                    "reason_code": "valuation_requirement_domain_object_missing",
                },
            )
            continue
        quantity = item.quantity
        reward_lot = (
            item
            if isinstance(item, AcquisitionLot)
            and item.acquisition_type
            in {
                AcquisitionType.STAKING_REWARD,
                AcquisitionType.LEGACY_STAKING_REWARD,
            }
            else None
        )
        if reward_lot is not None:
            try:
                calculate_reward_valuation(
                    net_quantity=quantity,
                    gross_quantity=reward_lot.gross_quantity,
                    fee_quantity=reward_lot.fee_quantity,
                    asset_code=reward_lot.asset_code,
                    fee_asset=reward_lot.fee_asset,
                    unit_price_eur=Decimal("1"),
                    method_version=method_version,
                )
            except RewardValuationError as error:
                run.review_count += 1
                audit(
                    "valuation.review_created",
                    {
                        "requirement_id": str(requirement.id),
                        "valuation_run_id": str(run.id),
                        "domain_object_type": requirement.domain_object_type,
                        "domain_object_id": str(requirement.domain_object_id),
                        "asset_code": requirement.asset_code,
                        "valuation_at": requirement.valuation_at.isoformat(),
                        "price_date": requirement.valuation_at.date().isoformat(),
                        "method": PriceMethod.DAILY_AVERAGE_HOURLY.value,
                        "quantity": str(quantity),
                        "net_quantity": str(quantity),
                        "gross_quantity": (
                            str(reward_lot.gross_quantity)
                            if reward_lot.gross_quantity is not None
                            else None
                        ),
                        "fee_quantity": (
                            str(reward_lot.fee_quantity)
                            if reward_lot.fee_quantity is not None
                            else None
                        ),
                        "method_version": method_version,
                        "status": ValuationDecisionStatus.REVIEW_REQUIRED.value,
                        "reason_code": error.code,
                        "valuation_basis": "staking_reward_components_v2",
                        "rounding_rule": "ROUND_HALF_UP_DISPLAY_ONLY",
                        "version": 1,
                    },
                )
                continue
        try:
            if requirement.method == ValuationMethod.DIRECT_EUR:
                value = (
                    item.native_consideration_quantity or quantity
                    if isinstance(item, (AcquisitionLot, DisposalEvent))
                    else quantity
                )
                unit = value / quantity
                method = PriceMethod.NATIVE_EUR
                source = "Kraken-Ausführung"
                price_id = None
                samples = 1
                provider_name = "kraken"
                provider_contract_version = "kraken-import-v1"
                decision_provider_evidence_id = None
                audit(
                    "valuation.native_eur_applied",
                    {"requirement_id": str(requirement.id)},
                )
            else:
                key = (requirement.asset_code, requirement.valuation_at.date())
                daily = cache.get(key)
                if daily is None:
                    candidate_prices = [
                        item
                        for item in list_entities(db, DailyPrice)
                        if item.asset_code == key[0] and item.price_date == key[1]
                    ]
                    automatic = [
                        item
                        for item in candidate_prices
                        if item.method == PriceMethod.DAILY_AVERAGE_HOURLY
                        and item.provider == provider.name
                        and item.provider_contract_version == provider.contract_version
                    ]
                    batch = batches[key]
                    if isinstance(batch, PriceProviderError):
                        raise batch
                    observations = batch.observations
                    provider_evidence = batch.evidence
                    observation_hash = evidence_hash(observations)
                    identical = next(
                        (
                            item
                            for item in automatic
                            if item.evidence_hash == observation_hash
                        ),
                        None,
                    )
                    if identical:
                        daily = identical
                        audit(
                            "valuation.duplicate_detected",
                            {
                                "daily_price_id": str(daily.id),
                                "kind": "provider_evidence",
                            },
                        )
                    else:
                        average, decision_status, reason = daily_average(
                            observations, key[1], now=now
                        )
                        previous_automatic = (
                            max(automatic, key=lambda item: item.version)
                            if automatic
                            else None
                        )
                        daily = DailyPrice(
                            asset_code=key[0],
                            price_date=key[1],
                            unit_price_eur=average,
                            method=PriceMethod.DAILY_AVERAGE_HOURLY,
                            source="CoinGecko Market Chart Range",
                            provider=provider.name,
                            provider_contract_version=(provider.contract_version),
                            evidence_hash=observation_hash,
                            sample_count=len(observations),
                            earliest_sample_at=min(x.observed_at for x in observations),
                            latest_sample_at=max(x.observed_at for x in observations),
                            minimum_price_eur=min(x.price_eur for x in observations),
                            maximum_price_eur=max(x.price_eur for x in observations),
                            fetched_at=now,
                            status=decision_status,
                            version=(
                                previous_automatic.version + 1
                                if previous_automatic
                                else 1
                            ),
                            supersedes_id=(
                                previous_automatic.id if previous_automatic else None
                            ),
                            provider_evidence_id=provider_evidence.id,
                        )
                        db.add(daily)
                        db.flush()
                        audit(
                            "valuation.daily_price_created",
                            {"daily_price_id": str(daily.id)},
                        )
                        if previous_automatic:
                            audit(
                                "valuation.conflict_detected",
                                {
                                    "previous_daily_price_id": str(
                                        previous_automatic.id
                                    ),
                                    "new_daily_price_id": str(daily.id),
                                    "reason": "provider_evidence_changed",
                                },
                            )
                    cache[key] = daily
                unit = daily.unit_price_eur
                method = daily.method
                source = daily.source
                price_id = daily.id
                samples = daily.sample_count
                provider_name = daily.provider
                provider_contract_version = daily.provider_contract_version
                reason = (
                    "valuation_manual_daily_price"
                    if daily.method == PriceMethod.MANUAL_DAILY_PRICE
                    else (
                        "valuation_incomplete_daily_coverage"
                        if daily.status == ValuationDecisionStatus.REVIEW_REQUIRED
                        else "valuation_resolved"
                    )
                )
                decision_status = daily.status
                value = calculate_eur_value(quantity, unit)
                decision_provider_evidence_id = daily.provider_evidence_id
            reward_components: RewardValuationComponents | None = None
            if reward_lot is not None:
                reward_components = calculate_reward_valuation(
                    net_quantity=quantity,
                    gross_quantity=reward_lot.gross_quantity,
                    fee_quantity=reward_lot.fee_quantity,
                    asset_code=reward_lot.asset_code,
                    fee_asset=reward_lot.fee_asset,
                    unit_price_eur=unit,
                    method_version=method_version,
                )
                value = reward_components.net_acquisition_value_eur
            previous = decisions_by_requirement.get(requirement.id, [])
            previous.sort(key=lambda item: item.version, reverse=True)
            decision = ValuationDecision(
                valuation_requirement_id=requirement.id,
                valuation_run_id=run.id,
                domain_object_type=requirement.domain_object_type,
                domain_object_id=requirement.domain_object_id,
                asset_code=requirement.asset_code,
                quantity=quantity,
                valuation_at=requirement.valuation_at,
                price_date=requirement.valuation_at.date(),
                method=method,
                unit_price_eur=unit,
                eur_value=value,
                price_source=source,
                provider=provider_name,
                provider_object_id=price_id,
                provider_evidence_id=decision_provider_evidence_id,
                provider_contract_version=provider_contract_version,
                method_version=method_version,
                sample_count=samples,
                fetched_at=now,
                decided_at=now,
                status=(
                    decision_status
                    if requirement.method != ValuationMethod.DIRECT_EUR
                    else ValuationDecisionStatus.RESOLVED
                ),
                reason_code=(
                    reason
                    if requirement.method != ValuationMethod.DIRECT_EUR
                    else "valuation_native_eur"
                ),
                version=(previous[0].version + 1 if previous else 1),
                supersedes_id=(previous[0].id if previous else None),
                gross_quantity=(
                    reward_components.gross_quantity if reward_components else None
                ),
                fee_quantity=(
                    reward_components.fee_quantity if reward_components else None
                ),
                net_quantity=quantity,
                gross_income_eur=(
                    reward_components.gross_income_eur if reward_components else None
                ),
                fee_value_eur=(
                    reward_components.fee_value_eur if reward_components else None
                ),
                net_acquisition_value_eur=(
                    value if isinstance(item, AcquisitionLot) else None
                ),
                valuation_basis=(
                    reward_components.valuation_basis
                    if reward_components
                    else "net_quantity"
                ),
                fee_tax_classification=(
                    reward_components.fee_tax_classification
                    if reward_components
                    else FeeTaxClassification.NOT_APPLICABLE
                ),
                fee_tax_review_status=(
                    reward_components.fee_tax_review_status
                    if reward_components
                    else FeeTaxReviewStatus.NOT_REQUIRED
                ),
            )
            db.add(decision)
            if previous:
                audit(
                    "valuation.decision_superseded",
                    {
                        "previous_decision_id": str(previous[0].id),
                        "new_decision_id": str(decision.id),
                        "reason": "method_version_changed",
                    },
                )
            if decision.status == ValuationDecisionStatus.RESOLVED:
                run.resolved_requirements += 1
                if decision.gross_income_eur is not None:
                    gross_income_values.append(decision.gross_income_eur)
                if (
                    decision.fee_value_eur is not None
                    and decision.fee_tax_classification
                    is FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
                ):
                    fee_candidate_values.append(decision.fee_value_eur)
                if decision.net_acquisition_value_eur is not None:
                    net_acquisition_values.append(decision.net_acquisition_value_eur)
                if method == PriceMethod.NATIVE_EUR:
                    run.native_count += 1
                elif method == PriceMethod.MANUAL_DAILY_PRICE:
                    run.manual_count += 1
                else:
                    run.automatic_count += 1
                audit(
                    "valuation.automatic_decision_created",
                    {"decision_id": str(decision.id)},
                )
            else:
                run.review_count += 1
                audit(
                    "valuation.review_created",
                    {
                        "decision_id": str(decision.id),
                        "reason_code": decision.reason_code,
                    },
                )
        except PriceProviderError as error:
            run.review_count += 1
            audit(
                "valuation.provider_fetch_failed",
                {
                    "requirement_id": str(requirement.id),
                    "code": error.code,
                    "temporary": error.temporary,
                },
            )
    transition_valuation_run(
        run,
        (
            ValuationRunStatus.COMPLETED_WITH_REVIEW
            if run.review_count
            else ValuationRunStatus.COMPLETED
        ),
        utc_now(),
    )
    audit(
        (
            "valuation.run_completed_with_review"
            if run.review_count
            else "valuation.run_completed"
        ),
        {"resolved": run.resolved_requirements, "reviews": run.review_count},
    )
    db.commit()
    return {
        "id": str(run.id),
        "status": run.status.value,
        "method_version": run.method_version,
        "checked": run.checked_requirements,
        "resolved": run.resolved_requirements,
        "reviews": run.review_count,
        "gross_income_total_eur": str(exact_decimal_sum(gross_income_values)),
        "fee_candidate_total_eur": str(exact_decimal_sum(fee_candidate_values)),
        "net_acquisition_total_eur": str(exact_decimal_sum(net_acquisition_values)),
    }


@router.get("/valuations", response_model=PageResponse)
def valuation_list(
    db: Db,
    offset: Offset = 0,
    limit: Limit = 100,
    asset: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9._-]+$")] = None,
    status: ValuationDecisionStatus | None = None,
    method: PriceMethod | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(
            422,
            detail={
                "code": "valuation_invalid_date_range",
                "message": "Das Startdatum liegt nach dem Enddatum.",
            },
        )
    decisions = list_entities(db, ValuationDecision)
    superseded_ids = {x.supersedes_id for x in decisions if x.supersedes_id}
    rows = [
        {
            "id": str(x.id),
            "asset": x.asset_code,
            "date": x.price_date,
            "method": x.method.value,
            "unit_price_eur": str(x.unit_price_eur),
            "eur_value": str(x.eur_value),
            "quantity": str(x.quantity),
            "net_quantity": str(x.net_quantity) if x.net_quantity is not None else None,
            "gross_quantity": (
                str(x.gross_quantity) if x.gross_quantity is not None else None
            ),
            "fee_quantity": (
                str(x.fee_quantity) if x.fee_quantity is not None else None
            ),
            "gross_income_eur": (
                str(x.gross_income_eur) if x.gross_income_eur is not None else None
            ),
            "fee_value_eur": (
                str(x.fee_value_eur) if x.fee_value_eur is not None else None
            ),
            "net_acquisition_value_eur": (
                str(x.net_acquisition_value_eur)
                if x.net_acquisition_value_eur is not None
                else None
            ),
            "valuation_basis": x.valuation_basis,
            "fee_tax_classification": (
                x.fee_tax_classification.value
                if x.fee_tax_classification is not None
                else None
            ),
            "fee_tax_review_status": (
                x.fee_tax_review_status.value
                if x.fee_tax_review_status is not None
                else None
            ),
            "method_version": x.method_version,
            "rounding_rule": x.rounding_rule,
            "source": x.price_source,
            "provider": x.provider,
            "samples": x.sample_count,
            "status": x.status.value,
            "effective_status": (
                ValuationDecisionStatus.SUPERSEDED.value
                if x.id in superseded_ids
                else x.status.value
            ),
            "version": x.version,
            "fetched_at": x.fetched_at,
        }
        for x in decisions
        if (asset is None or x.asset_code == asset.upper())
        and (status is None or x.status == status)
        and (method is None or x.method == method)
        and (date_from is None or x.price_date >= date_from)
        and (date_to is None or x.price_date <= date_to)
    ]
    rows += [
        {
            "id": str(audit.id),
            "asset": str(audit.metadata["asset_code"]),
            "date": date.fromisoformat(str(audit.metadata["price_date"])),
            "method": str(audit.metadata["method"]),
            "unit_price_eur": None,
            "eur_value": None,
            "quantity": str(audit.metadata["quantity"]),
            "net_quantity": str(audit.metadata["net_quantity"]),
            "gross_quantity": audit.metadata["gross_quantity"],
            "fee_quantity": audit.metadata["fee_quantity"],
            "gross_income_eur": None,
            "fee_value_eur": None,
            "net_acquisition_value_eur": None,
            "valuation_basis": str(audit.metadata["valuation_basis"]),
            "fee_tax_classification": None,
            "fee_tax_review_status": FeeTaxReviewStatus.REVIEW_REQUIRED.value,
            "method_version": str(audit.metadata["method_version"]),
            "rounding_rule": str(audit.metadata["rounding_rule"]),
            "source": None,
            "provider": None,
            "samples": 0,
            "status": ValuationDecisionStatus.REVIEW_REQUIRED.value,
            "effective_status": ValuationDecisionStatus.REVIEW_REQUIRED.value,
            "version": int(audit.metadata["version"]),
            "fetched_at": None,
            "reason_code": str(audit.metadata["reason_code"]),
        }
        for audit in list_entities(db, AuditEvent)
        if audit.event_type == "valuation.review_created"
        and "decision_id" not in audit.metadata
        and "valuation_basis" in audit.metadata
        and (asset is None or audit.metadata["asset_code"] == asset.upper())
        and (status is None or status == ValuationDecisionStatus.REVIEW_REQUIRED)
        and (method is None or audit.metadata["method"] == method.value)
        and (
            date_from is None
            or date.fromisoformat(str(audit.metadata["price_date"])) >= date_from
        )
        and (
            date_to is None
            or date.fromisoformat(str(audit.metadata["price_date"])) <= date_to
        )
    ]
    return page(rows, offset, limit)


@router.get("/valuations/{item_id}", response_model=DetailResponse)
def valuation_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    from app.core.entities import AuditEvent, ImportSession, RawImportRecord
    from app.core.transformation import DomainProvenance, TransformationRun

    item = db.get(ValuationDecision, item_id)
    if item is None:
        review = db.get(AuditEvent, item_id)
        if (
            review is None
            or review.event_type != "valuation.review_created"
            or "valuation_basis" not in review.metadata
        ):
            raise not_found("valuation_not_found")
        return {
            "id": str(review.id),
            "asset": str(review.metadata["asset_code"]),
            "quantity": str(review.metadata["quantity"]),
            "net_quantity": str(review.metadata["net_quantity"]),
            "gross_quantity": review.metadata["gross_quantity"],
            "fee_quantity": review.metadata["fee_quantity"],
            "unit_price_eur": None,
            "eur_value": None,
            "gross_income_eur": None,
            "fee_value_eur": None,
            "net_acquisition_value_eur": None,
            "valuation_basis": str(review.metadata["valuation_basis"]),
            "fee_tax_classification": None,
            "fee_tax_review_status": FeeTaxReviewStatus.REVIEW_REQUIRED.value,
            "method_version": str(review.metadata["method_version"]),
            "rounding_rule": str(review.metadata["rounding_rule"]),
            "method": str(review.metadata["method"]),
            "status": ValuationDecisionStatus.REVIEW_REQUIRED.value,
            "effective_status": ValuationDecisionStatus.REVIEW_REQUIRED.value,
            "version": int(review.metadata["version"]),
            "supersedes_id": None,
            "superseded_by_id": None,
            "reason_code": str(review.metadata["reason_code"]),
            "requirement": {
                "id": str(review.metadata["requirement_id"]),
                "transformation_run_id": None,
            },
            "provider_evidence_id": None,
            "valuation_run": {
                "id": str(review.metadata["valuation_run_id"]),
                "status": ValuationRunStatus.COMPLETED_WITH_REVIEW.value,
                "method_version": str(review.metadata["method_version"]),
            },
            "domain_object": {
                "type": str(review.metadata["domain_object_type"]),
                "id": str(review.metadata["domain_object_id"]),
            },
            "daily_price": None,
            "provider_evidence": None,
            "import_sessions": [],
            "transformation_runs": [],
            "raw_records": [],
            "audit": [
                {
                    "event_type": review.event_type,
                    "occurred_at": review.occurred_at,
                    "metadata": review.metadata,
                }
            ],
        }
    requirement = db.get(ValuationRequirement, item.valuation_requirement_id)
    valuation_run = db.get(ValuationRun, item.valuation_run_id)
    daily_price = (
        db.get(DailyPrice, item.provider_object_id) if item.provider_object_id else None
    )
    provider_evidence = (
        db.get(ProviderEvidence, item.provider_evidence_id)
        if item.provider_evidence_id
        else None
    )
    provenance = [
        x
        for x in list_entities(db, DomainProvenance)
        if x.domain_object_id == item.domain_object_id
    ]
    raw_ids = {x.raw_import_record_id for x in provenance}
    raw = [x for x in list_entities(db, RawImportRecord) if x.id in raw_ids]
    import_ids = {x.import_session_id for x in provenance}
    import_sessions = [
        x for x in list_entities(db, ImportSession) if x.id in import_ids
    ]
    transformation_ids = {x.transformation_run_id for x in provenance}
    transformation_runs = [
        x for x in list_entities(db, TransformationRun) if x.id in transformation_ids
    ]
    domain_model = {
        **VALUABLE_DOMAIN_MODELS,
        "TradeExecution": TradeExecution,
        "trade_execution": TradeExecution,
    }.get(item.domain_object_type)
    domain_object = (
        db.get(domain_model, item.domain_object_id) if domain_model else None
    )
    successor = next(
        (
            decision
            for decision in list_entities(db, ValuationDecision)
            if decision.supersedes_id == item.id
        ),
        None,
    )
    audits = [
        x
        for x in list_entities(db, AuditEvent)
        if x.entity_id in {item.id, item.valuation_run_id}
    ]
    return {
        "id": str(item.id),
        "asset": item.asset_code,
        "quantity": str(item.quantity),
        "net_quantity": (
            str(item.net_quantity) if item.net_quantity is not None else None
        ),
        "gross_quantity": (
            str(item.gross_quantity) if item.gross_quantity is not None else None
        ),
        "fee_quantity": (
            str(item.fee_quantity) if item.fee_quantity is not None else None
        ),
        "unit_price_eur": str(item.unit_price_eur),
        "eur_value": str(item.eur_value),
        "gross_income_eur": (
            str(item.gross_income_eur) if item.gross_income_eur is not None else None
        ),
        "fee_value_eur": (
            str(item.fee_value_eur) if item.fee_value_eur is not None else None
        ),
        "net_acquisition_value_eur": (
            str(item.net_acquisition_value_eur)
            if item.net_acquisition_value_eur is not None
            else None
        ),
        "valuation_basis": item.valuation_basis,
        "fee_tax_classification": (
            item.fee_tax_classification.value
            if item.fee_tax_classification is not None
            else None
        ),
        "fee_tax_review_status": (
            item.fee_tax_review_status.value
            if item.fee_tax_review_status is not None
            else None
        ),
        "method_version": item.method_version,
        "rounding_rule": item.rounding_rule,
        "method": item.method.value,
        "status": item.status.value,
        "effective_status": (
            ValuationDecisionStatus.SUPERSEDED.value if successor else item.status.value
        ),
        "version": item.version,
        "supersedes_id": str(item.supersedes_id) if item.supersedes_id else None,
        "superseded_by_id": str(successor.id) if successor else None,
        "requirement": (
            {
                "id": str(requirement.id),
                "transformation_run_id": str(requirement.transformation_run_id),
            }
            if requirement
            else None
        ),
        "provider_evidence_id": (
            str(item.provider_evidence_id) if item.provider_evidence_id else None
        ),
        "valuation_run": (
            {
                "id": str(valuation_run.id),
                "status": valuation_run.status.value,
                "method_version": valuation_run.method_version,
                "provider": valuation_run.provider,
                "started_at": valuation_run.started_at,
                "ended_at": valuation_run.ended_at,
            }
            if valuation_run
            else None
        ),
        "domain_object": (
            {
                "type": item.domain_object_type,
                "id": str(domain_object.id),
                "external_id": getattr(domain_object, "external_id", None),
                "occurred_at": getattr(domain_object, "occurred_at", None),
            }
            if domain_object
            else None
        ),
        "daily_price": (
            {
                "id": str(daily_price.id),
                "method": daily_price.method.value,
                "price_eur": str(daily_price.unit_price_eur),
                "source": daily_price.source,
                "version": daily_price.version,
            }
            if daily_price
            else None
        ),
        "provider_evidence": (
            {
                "id": str(provider_evidence.id),
                "provider": provider_evidence.provider,
                "provider_contract_version": (
                    provider_evidence.provider_contract_version
                ),
                "provider_asset_id": provider_evidence.provider_asset_id,
                "target_currency": provider_evidence.target_currency,
                "requested_from": provider_evidence.requested_from,
                "requested_to": provider_evidence.requested_to,
                "fetched_at": provider_evidence.fetched_at,
                "http_status": provider_evidence.http_status,
                "response_hash": provider_evidence.response_hash,
                "observation_count": provider_evidence.observation_count,
                "earliest_observed_at": provider_evidence.earliest_observed_at,
                "latest_observed_at": provider_evidence.latest_observed_at,
            }
            if provider_evidence
            else None
        ),
        "import_sessions": [
            {
                "id": str(session.id),
                "source": session.source,
                "status": session.status.value,
                "imported_at": session.started_at,
            }
            for session in import_sessions
        ],
        "transformation_runs": [
            {
                "id": str(run.id),
                "status": run.status.value,
                "contract_version": run.contract_version,
            }
            for run in transformation_runs
        ],
        "raw_records": [
            {
                "id": str(x.id),
                "session_id": str(x.import_session_id),
                "external_id": x.external_id,
            }
            for x in raw
        ],
        "audit": [
            {
                "event_type": x.event_type,
                "occurred_at": x.occurred_at,
                "metadata": x.metadata,
            }
            for x in audits
        ],
    }


@router.get("/reviews", response_model=PageResponse)
def reviews(db: Db, offset: Offset = 0, limit: Limit = 100) -> dict[str, Any]:
    from app.core.entities import AuditEvent
    from app.core.transformation import TransformationIssue

    rows = [
        {
            "id": str(x.id),
            "code": x.code,
            "message": x.message,
            "kind": "transformation",
            "occurred_at": x.occurred_at,
        }
        for x in list_entities(db, TransformationIssue)
    ]
    rows += [
        {
            "id": str(x.id),
            "code": x.reason_code,
            "message": "Bewertung erfordert fachliche Prüfung.",
            "kind": "valuation",
            "occurred_at": x.decided_at,
        }
        for x in list_entities(db, ValuationDecision)
        if x.status == ValuationDecisionStatus.REVIEW_REQUIRED
    ]
    rows += [
        {
            "id": str(x.id),
            "code": str(x.metadata.get("code", "valuation_provider_unavailable")),
            "message": "Der Kursprovider konnte die Bewertung nicht abschließen.",
            "kind": "valuation",
            "occurred_at": x.occurred_at,
        }
        for x in list_entities(db, AuditEvent)
        if x.event_type == "valuation.provider_fetch_failed"
    ]
    rows += [
        {
            "id": str(x.id),
            "code": str(x.metadata["reason_code"]),
            "message": "Bewertung erfordert fachliche Prüfung.",
            "kind": "valuation",
            "occurred_at": x.occurred_at,
        }
        for x in list_entities(db, AuditEvent)
        if x.event_type == "valuation.review_created"
        and "reason_code" in x.metadata
        and "decision_id" not in x.metadata
    ]
    rows += [
        {
            "id": str(x.id),
            "code": x.code,
            "message": x.message,
            "kind": "tax",
            "occurred_at": x.occurred_at,
        }
        for x in list_entities(db, TaxReviewCase)
    ]
    rows.sort(key=lambda row: str(row["occurred_at"]), reverse=True)
    return page(rows, offset, limit)


@router.get("/reviews/{item_id}", response_model=DetailResponse)
def review_detail(item_id: UUID, db: Db) -> dict[str, Any]:
    from app.core.entities import AuditEvent
    from app.core.transformation import TransformationIssue

    issue = db.get(TransformationIssue, item_id)
    decision = db.get(ValuationDecision, item_id)
    audit = db.get(AuditEvent, item_id)
    tax_review = db.get(TaxReviewCase, item_id)
    if issue:
        return {
            "id": str(issue.id),
            "code": issue.code,
            "message": issue.message,
            "kind": "transformation",
        }
    if decision and decision.status == ValuationDecisionStatus.REVIEW_REQUIRED:
        return {
            "id": str(decision.id),
            "code": decision.reason_code,
            "message": "Bewertung erfordert fachliche Prüfung.",
            "kind": "valuation",
        }
    if audit and audit.event_type in {
        "valuation.provider_fetch_failed",
        "valuation.review_created",
    }:
        code = audit.metadata.get(
            "code",
            audit.metadata.get("reason_code", "valuation_provider_unavailable"),
        )
        return {
            "id": str(audit.id),
            "code": str(code),
            "message": (
                "Bewertung erfordert fachliche Prüfung."
                if audit.event_type == "valuation.review_created"
                else "Der Kursprovider konnte die Bewertung nicht abschließen."
            ),
            "kind": "valuation",
            "audit": [
                {
                    "event_type": audit.event_type,
                    "occurred_at": audit.occurred_at,
                    "metadata": audit.metadata,
                }
            ],
        }
    if tax_review:
        return {
            "id": str(tax_review.id),
            "code": tax_review.code,
            "message": tax_review.message,
            "kind": "tax",
            "source_object_type": tax_review.source_object_type,
            "source_object_id": str(tax_review.source_object_id),
            "tax_calculation_run_id": str(tax_review.tax_calculation_run_id),
            "audit": [
                {
                    "event_type": event.event_type,
                    "occurred_at": event.occurred_at,
                    "metadata": event.metadata,
                }
                for event in list_entities(db, AuditEvent)
                if event.entity_id == tax_review.tax_calculation_run_id
            ],
        }
    raise not_found("review_not_found")


@router.get("/system/status", response_model=SystemStatusResponse)
@router.get("/system", include_in_schema=False)
def system_status(db: Db) -> dict[str, Any]:
    settings = get_settings()
    return {
        "backend": True,
        "database": db.scalar(text("SELECT 1")) == 1,
        "migration": "0008_reward_valuation_components",
        "coingecko_mode": settings.coingecko_api_mode,
        "api_key_configured": bool(settings.coingecko_api_key),
        "method_version": METHOD_VERSION,
        "asset_mapping_version": MAPPING_VERSION,
    }
