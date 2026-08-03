from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from app.adapters.kraken.ledger import (
    LEDGER_ASSET_MAPPING_VERSION,
    LEDGER_NORMALIZATION_VERSION,
    ParsedLedgerBatch,
    canonical_fingerprint,
    canonical_from_api,
    compare_ledgers,
    filter_records,
    ledger_digest,
    parse_ledger_csv,
)
from app.adapters.kraken.transformation import (
    TRANSFORMATION_CONTRACT_VERSION,
    KrakenTransformationService,
)
from app.config.settings import Settings, get_settings
from app.core.entities import AuditActorType, ImportSession, ImportStatus
from app.core.identifiers import Uuid4IdGenerator
from app.core.time import utc_now
from app.database.session import get_session
from app.database.unit_of_work import SqlAlchemyUnitOfWork
from app.imports.context import ImportContext
from app.imports.service import ImportService, RawRecordInput
from app.imports.validation import RequiredFieldsValidator
from app.infrastructure.kraken_private import (
    KrakenPrivateClient,
    KrakenPrivateError,
    LedgerPreview,
)

router = APIRouter(prefix="/api/kraken", tags=["Kraken-Live-Vorschau"])
Db = Annotated[Session, Depends(get_session)]


class ConnectionResponse(BaseModel):
    configured: bool
    reachable: bool
    authenticated: bool
    ledger_permission_available: bool
    message: str


class LedgerPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime | None = None
    end: datetime | None = None
    asset: str | None = Field(default=None, min_length=1, max_length=32)
    ledger_type: str = Field(default="all", min_length=1, max_length=32)
    diagnostic_limit: int = Field(default=0, ge=0, le=100)


class DiagnosticEntryResponse(BaseModel):
    ledger_id: str
    occurred_at: datetime
    entry_type: str
    subtype: str
    asset: str


class LedgerPreviewResponse(BaseModel):
    connection_status: str
    requested_start: datetime | None
    requested_end: datetime | None
    fetched_pages: int
    reported_total: int
    received_total: int
    unique_total: int
    duplicate_ids: list[str]
    conflicting_duplicate_ids: list[str]
    earliest_entry_at: datetime | None
    latest_entry_at: datetime | None
    counts_by_type: dict[str, int]
    counts_by_subtype: dict[str, int]
    counts_by_asset: dict[str, int]
    unknown_types: list[str]
    unknown_subtypes: list[str]
    malformed_entries: int
    pagination_complete: bool
    stable_ledger_id_digest: str
    warnings: list[str]
    ready_for_import: bool
    diagnostics: list[DiagnosticEntryResponse]


class LedgerImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    asset: str | None = None
    ledger_type: str = "all"
    expected_ledger_id_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    explicit_confirmation: bool
    transform: bool = False


class LedgerComparisonResponse(BaseModel):
    requested_start: datetime
    requested_end: datetime
    csv_total: int
    api_total: int
    csv_unique_total: int
    api_unique_total: int
    matched_ids: int
    missing_in_api: list[str]
    missing_in_csv: list[str]
    duplicate_ids_csv: list[str]
    duplicate_ids_api: list[str]
    conflicting_duplicates_csv: list[str]
    conflicting_duplicates_api: list[str]
    field_mismatch_count: int
    mismatches_by_field: dict[str, int]
    exact_match_count: int
    normalized_match_count: int
    timestamp_precision_only_count: int
    not_comparable_fields: list[str]
    unknown_asset_mappings: list[str]
    unknown_type_mappings: list[str]
    csv_ledger_id_digest: str
    api_ledger_id_digest: str
    digests_match: bool
    pagination_complete: bool
    warnings: list[str]
    ready_for_import: bool
    diagnostics: list[dict[str, str]]


class LedgerImportResponse(BaseModel):
    import_session_id: str
    source_kind: str
    requested_start: datetime
    requested_end: datetime
    fetched_pages: int
    received_total: int
    unique_total: int
    created_records: int
    reused_records: int
    conflicting_records: int
    digest: str
    transformed: bool
    transformation_summary: dict[str, Any] | None
    review_cases: int
    status: str


def build_kraken_client(settings: Settings) -> KrakenPrivateClient:
    if settings.kraken_api_base_url.rstrip(
        "/"
    ) != "https://api.kraken.com" and settings.environment not in {"test", "testing"}:
        raise KrakenPrivateError(
            "kraken_base_url_invalid",
            "Eine abweichende Kraken-Basisadresse ist nur in Tests zulässig.",
        )
    return KrakenPrivateClient(
        api_key=settings.kraken_api_key or "",
        api_secret=settings.kraken_api_secret or "",
        base_url=settings.kraken_api_base_url,
        timeout=settings.kraken_api_timeout,
        max_retries=settings.kraken_api_max_retries,
    )


_STATUS_BY_CODE = {
    "kraken_not_configured": 503,
    "kraken_configuration_invalid": 503,
    "kraken_base_url_invalid": 503,
    "kraken_authentication_failed": 401,
    "kraken_ledger_permission_missing": 403,
    "kraken_invalid_nonce": 502,
    "kraken_rate_limited": 429,
    "kraken_timeout": 504,
    "kraken_unavailable": 503,
    "kraken_invalid_response": 502,
    "kraken_api_error": 502,
}
_LEDGER_TYPES = {
    "all",
    "trade",
    "deposit",
    "withdrawal",
    "transfer",
    "margin",
    "adjustment",
    "rollover",
    "credit",
    "settled",
    "staking",
    "dividend",
    "sale",
    "nft_rebate",
}


def _http_error(error: KrakenPrivateError) -> HTTPException:
    return HTTPException(
        status_code=_STATUS_BY_CODE.get(error.code, 502),
        detail={"code": error.code, "message": error.message},
    )


@router.get("/connection", response_model=ConnectionResponse)
def connection(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ConnectionResponse:
    if not settings.kraken_api_key or not settings.kraken_api_secret:
        return ConnectionResponse(
            configured=False,
            reachable=False,
            authenticated=False,
            ledger_permission_available=False,
            message="Kraken-Lesezugriff ist serverseitig nicht konfiguriert.",
        )
    try:
        build_kraken_client(settings).check_ledger_access()
    except KrakenPrivateError as error:
        if error.code == "kraken_ledger_permission_missing":
            return ConnectionResponse(
                configured=True,
                reachable=True,
                authenticated=True,
                ledger_permission_available=False,
                message=error.message,
            )
        if error.code == "kraken_authentication_failed":
            return ConnectionResponse(
                configured=True,
                reachable=True,
                authenticated=False,
                ledger_permission_available=False,
                message=error.message,
            )
        return ConnectionResponse(
            configured=True,
            reachable=False,
            authenticated=False,
            ledger_permission_available=False,
            message=error.message,
        )
    return ConnectionResponse(
        configured=True,
        reachable=True,
        authenticated=True,
        ledger_permission_available=True,
        message="Kraken-Ledger ist mit Leseberechtigung erreichbar.",
    )


@router.post("/ledger-preview", response_model=LedgerPreviewResponse)
def ledger_preview(
    request: Annotated[LedgerPreviewRequest, Body()],
    settings: Annotated[Settings, Depends(get_settings)],
) -> LedgerPreviewResponse:
    invalid_timezone = any(
        value is not None and (value.tzinfo is None or value.utcoffset() is None)
        for value in (request.start, request.end)
    )
    invalid_asset = request.asset is not None and not all(
        character.isascii() and (character.isalnum() or character in {".", ","})
        for character in request.asset
    )
    if invalid_timezone or invalid_asset or request.ledger_type not in _LEDGER_TYPES:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "kraken_invalid_filter",
                "message": "Die Ledger-Filter sind ungültig.",
            },
        )
    if request.start and request.end and request.end <= request.start:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "kraken_invalid_period",
                "message": "Das Ende muss nach dem Beginn liegen.",
            },
        )
    try:
        preview = build_kraken_client(settings).ledger_preview(
            start=request.start,
            end=request.end,
            asset=request.asset,
            ledger_type=request.ledger_type,
            diagnostic_limit=request.diagnostic_limit,
        )
    except KrakenPrivateError as error:
        raise _http_error(error) from error
    if not preview.ready_for_import:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "kraken_ledger_preview_incomplete",
                "message": (
                    "Die Ledger-Vorschau ist unvollständig oder widersprüchlich."
                ),
            },
        )
    return _preview_response(preview)


@router.post("/ledger-compare", response_model=LedgerComparisonResponse)
async def ledger_compare(
    file: Annotated[UploadFile, File()],
    start: Annotated[datetime, Form()],
    end: Annotated[datetime, Form()],
    settings: Annotated[Settings, Depends(get_settings)],
    asset: Annotated[str | None, Form()] = None,
    ledger_type: Annotated[str, Form()] = "all",
    diagnostic_limit: Annotated[int, Form(ge=0, le=100)] = 20,
) -> LedgerComparisonResponse:
    _validate_live_filters(start, end, asset, ledger_type)
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            400,
            detail={
                "code": "kraken_csv_file_required",
                "message": "Es wird eine Kraken-Ledger-CSV benötigt.",
            },
        )
    content = await file.read(settings.max_upload_bytes + 1)
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(
            413,
            detail={"code": "kraken_csv_too_large", "message": "Die CSV ist zu groß."},
        )
    try:
        csv_batch = parse_ledger_csv(content)
        preview = build_kraken_client(settings).ledger_preview(
            start=start,
            end=end,
            asset=asset,
            ledger_type=ledger_type,
            diagnostic_limit=0,
        )
    except ValueError as error:
        raise HTTPException(
            422,
            detail={"code": "kraken_csv_invalid", "message": str(error)},
        ) from error
    except KrakenPrivateError as error:
        raise _http_error(error) from error
    csv_records = filter_records(csv_batch.records, start, end)
    filtered_ids = {item.ledger_id for item in csv_records}
    filtered_batch = ParsedLedgerBatch(
        records=csv_records,
        duplicate_ids=tuple(
            item for item in csv_batch.duplicate_ids if item in filtered_ids
        ),
        conflicting_duplicate_ids=tuple(
            item for item in csv_batch.conflicting_duplicate_ids if item in filtered_ids
        ),
        malformed_entries=csv_batch.malformed_entries,
        warnings=csv_batch.warnings,
    )
    api_records = tuple(canonical_from_api(item) for item in preview.records)
    result = compare_ledgers(
        filtered_batch, api_records, diagnostic_limit=diagnostic_limit
    )
    ready = result.ready_for_import and preview.ready_for_import
    return LedgerComparisonResponse.model_validate(
        {
            "requested_start": start,
            "requested_end": end,
            "csv_total": result.csv_total,
            "api_total": result.api_total,
            "csv_unique_total": result.csv_unique_total,
            "api_unique_total": result.api_unique_total,
            "matched_ids": result.matched_ids,
            "missing_in_api": list(result.missing_in_api),
            "missing_in_csv": list(result.missing_in_csv),
            "duplicate_ids_csv": list(filtered_batch.duplicate_ids),
            "duplicate_ids_api": list(preview.duplicate_ids),
            "conflicting_duplicates_csv": list(
                filtered_batch.conflicting_duplicate_ids
            ),
            "conflicting_duplicates_api": list(preview.conflicting_duplicate_ids),
            "field_mismatch_count": result.field_mismatch_count,
            "mismatches_by_field": result.mismatches_by_field,
            "exact_match_count": result.exact_match_count,
            "normalized_match_count": result.normalized_match_count,
            "timestamp_precision_only_count": result.timestamp_precision_only_count,
            "not_comparable_fields": list(result.not_comparable_fields),
            "unknown_asset_mappings": list(result.unknown_asset_mappings),
            "unknown_type_mappings": list(result.unknown_type_mappings),
            "csv_ledger_id_digest": result.csv_digest,
            "api_ledger_id_digest": result.api_digest,
            "digests_match": result.csv_digest == result.api_digest,
            "pagination_complete": preview.pagination_complete,
            "warnings": [*result.warnings, *preview.warnings],
            "ready_for_import": ready,
            "diagnostics": [{"ledger_id": item} for item in result.diagnostic_ids],
        }
    )


@router.post("/ledger-import", response_model=LedgerImportResponse)
def ledger_import(
    request: LedgerImportRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    db: Db,
) -> LedgerImportResponse:
    if not request.explicit_confirmation:
        raise HTTPException(
            400,
            detail={
                "code": "kraken_confirmation_required",
                "message": "Der Ledger-Import muss ausdrücklich bestätigt werden.",
            },
        )
    _validate_live_filters(
        request.start, request.end, request.asset, request.ledger_type
    )
    try:
        preview = build_kraken_client(settings).ledger_preview(
            start=request.start,
            end=request.end,
            asset=request.asset,
            ledger_type=request.ledger_type,
            diagnostic_limit=0,
        )
    except KrakenPrivateError as error:
        raise _http_error(error) from error
    records = tuple(canonical_from_api(item) for item in preview.records)
    digest = ledger_digest(records)
    if digest != request.expected_ledger_id_digest:
        raise HTTPException(
            409,
            detail={
                "code": "kraken_ledger_changed",
                "message": "Das Kraken-Ledger hat sich seit dem Vergleich geändert.",
                "expected_digest": request.expected_ledger_id_digest,
                "actual_digest": digest,
            },
        )
    if not preview.ready_for_import:
        raise HTTPException(
            409,
            detail={
                "code": "kraken_ledger_incomplete",
                "message": "Das Kraken-Ledger ist nicht vollständig importierbar.",
            },
        )
    factory = _unit_of_work_factory(db)
    now = utc_now()
    imported_session = ImportSession(
        source="kraken-ledgers",
        version=LEDGER_NORMALIZATION_VERSION,
        status=ImportStatus.CREATED,
        started_at=now,
        correlation_id=Uuid4IdGenerator().new(),
        actor_type=AuditActorType.USER,
        actor_id="local-user",
    )
    context = ImportContext(
        session=imported_session,
        source=imported_session.source,
        version=imported_session.version,
        received_at=now,
        actor_type=imported_session.actor_type,
        actor_id=imported_session.actor_id,
        correlation_id=imported_session.correlation_id,
        source_name="Kraken Live API",
        metadata={
            "start": request.start.isoformat(),
            "end": request.end.isoformat(),
            "digest": digest,
            "asset": request.asset or "all",
            "ledger_type": request.ledger_type,
            "normalization_version": LEDGER_NORMALIZATION_VERSION,
            "asset_mapping_version": LEDGER_ASSET_MAPPING_VERSION,
        },
    )
    inputs = [
        RawRecordInput(
            payload=item.import_payload(),
            external_id=f"kraken:ledger:{item.ledger_id}",
            canonical_key=item.canonical_key,
            technical_metadata={
                "source_kind": item.source_kind.value,
                "canonical_fingerprint": canonical_fingerprint(item),
                "normalization_version": LEDGER_NORMALIZATION_VERSION,
                "asset_mapping_version": LEDGER_ASSET_MAPPING_VERSION,
                "canonical_asset": {
                    "raw_asset": item.asset_raw,
                    "normalized_asset": item.asset_normalized,
                    "product_marker": item.product_marker,
                    "product_variant": item.product_variant,
                    "is_unambiguous": item.asset_mapping_known,
                },
            },
        )
        for item in records
    ]
    result = ImportService(
        unit_of_work_factory=factory,
        id_generator=Uuid4IdGenerator(),
        validator=RequiredFieldsValidator(),
    ).import_records(context=context, records=inputs)
    if result.outcome.value == "failed":
        code = result.errors[0].code if result.errors else "kraken_import_failed"
        raise HTTPException(
            409,
            detail={
                "code": code,
                "message": "Der Kraken-Ledger-Import wurde atomar abgebrochen.",
            },
        )
    transformed = False
    transformation_summary: dict[str, Any] | None = None
    if request.transform:
        transformation_sessions = [
            *(result.reused_session_ids),
            *([result.session_id] if result.accepted_count else []),
        ]
        transformed_result = KrakenTransformationService(
            unit_of_work_factory=factory
        ).transform(import_session_ids=transformation_sessions, actor_id="local-user")
        transformed = True
        transformation_summary = {
            "run_id": str(transformed_result.run_id),
            "status": transformed_result.status.value,
            "checked": transformed_result.checked_records,
            "review_cases": transformed_result.review_cases,
            "contract_version": TRANSFORMATION_CONTRACT_VERSION,
            "created_objects": transformed_result.acquisitions
            + transformed_result.disposals
            + transformed_result.trade_executions
            + transformed_result.fee_events
            + transformed_result.valuation_requirements,
            "reused_objects": transformed_result.reused_objects,
        }
    return LedgerImportResponse.model_validate(
        {
            "import_session_id": str(result.session_id),
            "source_kind": "kraken_live_api",
            "requested_start": request.start,
            "requested_end": request.end,
            "fetched_pages": preview.fetched_pages,
            "received_total": preview.received_total,
            "unique_total": preview.unique_total,
            "created_records": result.accepted_count,
            "reused_records": result.reused_count,
            "conflicting_records": 0,
            "digest": digest,
            "transformed": transformed,
            "transformation_summary": transformation_summary,
            "review_cases": (
                transformation_summary["review_cases"] if transformation_summary else 0
            ),
            "status": result.outcome.value,
        }
    )


def _unit_of_work_factory(db: Session) -> Callable[[], SqlAlchemyUnitOfWork]:
    sessions: sessionmaker[Session] = sessionmaker(
        bind=db.get_bind(), autoflush=False, expire_on_commit=False
    )

    def create() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(sessions)

    return create


def _validate_live_filters(
    start: datetime, end: datetime, asset: str | None, ledger_type: str
) -> None:
    if (
        start.tzinfo is None
        or start.utcoffset() is None
        or end.tzinfo is None
        or end.utcoffset() is None
        or end <= start
        or ledger_type not in _LEDGER_TYPES
        or (
            asset is not None
            and not all(
                character.isascii() and (character.isalnum() or character in {".", ","})
                for character in asset
            )
        )
    ):
        raise HTTPException(
            400,
            detail={
                "code": "kraken_invalid_filter",
                "message": "Zeitraum oder Ledger-Filter sind ungültig.",
            },
        )


def _preview_response(preview: LedgerPreview) -> LedgerPreviewResponse:
    return LedgerPreviewResponse(
        connection_status="authenticated",
        requested_start=preview.requested_start,
        requested_end=preview.requested_end,
        fetched_pages=preview.fetched_pages,
        reported_total=preview.reported_total,
        received_total=preview.received_total,
        unique_total=preview.unique_total,
        duplicate_ids=list(preview.duplicate_ids),
        conflicting_duplicate_ids=list(preview.conflicting_duplicate_ids),
        earliest_entry_at=preview.earliest_entry_at,
        latest_entry_at=preview.latest_entry_at,
        counts_by_type=dict(preview.counts_by_type),
        counts_by_subtype=dict(preview.counts_by_subtype),
        counts_by_asset=dict(preview.counts_by_asset),
        unknown_types=list(preview.unknown_types),
        unknown_subtypes=list(preview.unknown_subtypes),
        malformed_entries=preview.malformed_entries,
        pagination_complete=preview.pagination_complete,
        stable_ledger_id_digest=preview.stable_ledger_id_digest,
        warnings=list(preview.warnings),
        ready_for_import=preview.ready_for_import,
        diagnostics=[
            DiagnosticEntryResponse(
                ledger_id=item.ledger_id,
                occurred_at=item.occurred_at,
                entry_type=item.entry_type,
                subtype=item.subtype,
                asset=item.asset,
            )
            for item in preview.diagnostics
        ],
    )
