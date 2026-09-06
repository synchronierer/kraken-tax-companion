from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entities import AuditActorType, AuditEvent, RawImportRecord
from app.core.financial_review import (
    FinancialReviewRecordLink,
    FinancialReviewResolution,
    FinancialReviewSuggestion,
    FinancialReviewType,
    FinancialSuggestionType,
    ResolutionStatus,
    SuggestionStatus,
)
from app.core.time import utc_now
from app.core.transformation import DomainProvenance, TransformationIssue
from app.database.mappings import (
    financial_review_record_links as financial_review_record_links_table,
)
from app.database.mappings import (
    financial_review_suggestions as financial_review_suggestions_table,
)
from app.database.session import get_session
from app.services.financial_reviews import (
    SuggestedReview,
    delisting_suggestions,
    fiat_withdrawal_suggestion,
    ledger_values,
    resolution_metadata,
)

router = APIRouter(prefix="/api", tags=["financial-reviews"])
Db = Annotated[Session, Depends(get_session)]


class ResolutionInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    status: Literal["confirmed", "rejected"]
    resolution_type: FinancialReviewType
    record_ids: list[UUID] = Field(min_length=1, max_length=2)
    decided_by: str = Field(default="local-user", min_length=1, max_length=255)
    reason: str = Field(min_length=1, max_length=1024)


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(409, detail={"code": code, "message": message})


def _raw_row(record: RawImportRecord, role: str) -> dict[str, Any]:
    values = ledger_values(record)
    return {
        "id": str(record.id),
        "external_id": record.external_id,
        "role": role,
        "time": values.get("time"),
        "type": values.get("type"),
        "asset": values.get("asset"),
        "amount": values.get("amount"),
        "fee": values.get("fee"),
        "refid": values.get("refid"),
    }


def _links(
    db: Session,
    *,
    suggestion_id: UUID | None = None,
    resolution_id: UUID | None = None,
) -> list[FinancialReviewRecordLink]:
    statement = select(FinancialReviewRecordLink)
    if suggestion_id is not None:
        statement = statement.where(
            financial_review_record_links_table.c.suggestion_id == suggestion_id
        )
    if resolution_id is not None:
        statement = statement.where(
            financial_review_record_links_table.c.resolution_id == resolution_id
        )
    return list(db.scalars(statement))


def _linked_records(
    db: Session, links: list[FinancialReviewRecordLink]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for link in links:
        record = db.get(RawImportRecord, link.raw_import_record_id)
        if record is not None:
            result.append(_raw_row(record, link.role))
    return result


def _resolution_row(db: Session, item: FinancialReviewResolution) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "resolution_type": item.resolution_type.value,
        "status": item.status.value,
        "created_at": item.created_at,
        "decided_at": item.decided_at,
        "decided_by": item.decided_by,
        "reason": item.reason,
        "source": item.source,
        "confidence": item.confidence.value if item.confidence else None,
        "tax_mapping_status": item.tax_mapping_status.value,
        "metadata": item.metadata,
        "records": _linked_records(db, _links(db, resolution_id=item.id)),
    }


def _suggestion_row(db: Session, item: FinancialReviewSuggestion) -> dict[str, Any]:
    resolution_type = (
        FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL
        if item.suggestion_type is FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL
        else FinancialReviewType.DELISTING_LIQUIDATION
    )
    return {
        "id": str(item.id),
        "suggestion_type": item.suggestion_type.value,
        "resolution_type": resolution_type.value,
        "status": item.status.value,
        "confidence": item.confidence.value,
        "reasons": item.reasons,
        "metadata": item.metadata,
        "created_at": item.created_at,
        "decided_at": item.decided_at,
        "decided_by": item.decided_by,
        "decision_reason": item.decision_reason,
        "records": _linked_records(db, _links(db, suggestion_id=item.id)),
    }


@router.post("/financial-review-suggestions")
def generate_suggestions(db: Db) -> dict[str, Any]:
    now = utc_now()
    issues = list(db.scalars(select(TransformationIssue)))
    issue_by_raw = {item.raw_import_record_id: item for item in issues}
    records = list(db.scalars(select(RawImportRecord)))
    trade_records = {
        item.raw_import_record_id
        for item in db.scalars(select(DomainProvenance))
        if item.domain_object_type == "TradeExecution"
    }
    resolved_records = {
        item.raw_import_record_id
        for item in db.scalars(select(FinancialReviewRecordLink))
        if item.resolution_id is not None
    }
    existing = {
        (item.transformation_issue_id, item.suggestion_type)
        for item in db.scalars(select(FinancialReviewSuggestion))
    }
    candidates: list[tuple[TransformationIssue, SuggestedReview]] = []
    for record in records:
        if record.id not in issue_by_raw or record.id in resolved_records:
            continue
        candidate = fiat_withdrawal_suggestion(
            record, has_trade_context=record.id in trade_records
        )
        if candidate is not None:
            candidates.append((issue_by_raw[record.id], candidate))
    excluded = (
        resolved_records
        | trade_records
        | (set(records_by_id(records)) - set(issue_by_raw))
    )
    for candidate in delisting_suggestions(records, excluded_record_ids=excluded):
        candidates.append((issue_by_raw[candidate.records[0][0].id], candidate))
    created: list[FinancialReviewSuggestion] = []
    for issue, candidate in candidates:
        key = (issue.id, candidate.suggestion_type)
        if key in existing:
            continue
        suggestion = FinancialReviewSuggestion(
            transformation_issue_id=issue.id,
            suggestion_type=candidate.suggestion_type,
            status=SuggestionStatus.SUGGESTED,
            confidence=candidate.confidence,
            reasons=list(candidate.reasons),
            metadata=candidate.metadata,
            created_at=now,
        )
        db.add(suggestion)
        for record, role in candidate.records:
            db.add(
                FinancialReviewRecordLink(
                    suggestion_id=suggestion.id,
                    raw_import_record_id=record.id,
                    role=role,
                )
            )
        db.add(
            AuditEvent(
                occurred_at=now,
                event_type="financial_review.suggestion_created",
                entity_type="FinancialReviewSuggestion",
                entity_id=suggestion.id,
                actor_type=AuditActorType.SYSTEM,
                actor_id="financial-review-suggestion-engine",
                metadata={
                    "transformation_issue_id": str(issue.id),
                    "suggestion_type": candidate.suggestion_type.value,
                    "record_ids": [str(item.id) for item, _ in candidate.records],
                    "confidence": candidate.confidence.value,
                },
            )
        )
        existing.add(key)
        created.append(suggestion)
    db.commit()
    return {"created_count": len(created), "created_ids": [str(x.id) for x in created]}


def records_by_id(records: list[RawImportRecord]) -> dict[UUID, RawImportRecord]:
    return {item.id: item for item in records}


@router.get("/financial-reviews")
def financial_reviews(db: Db) -> dict[str, Any]:
    issues = list(db.scalars(select(TransformationIssue)))
    suggestions = list(db.scalars(select(FinancialReviewSuggestion)))
    resolutions = list(db.scalars(select(FinancialReviewResolution)))
    resolution_by_raw = {
        link.raw_import_record_id: resolution
        for resolution in resolutions
        for link in _links(db, resolution_id=resolution.id)
    }
    suggestion_by_raw = {
        link.raw_import_record_id: suggestion
        for suggestion in suggestions
        for link in _links(db, suggestion_id=suggestion.id)
    }
    rows = []
    for issue in sorted(issues, key=lambda item: item.occurred_at, reverse=True):
        record = db.get(RawImportRecord, issue.raw_import_record_id)
        if record is None:
            continue
        suggestion = suggestion_by_raw.get(record.id)
        resolution = resolution_by_raw.get(record.id)
        rows.append(
            {
                "id": str(issue.id),
                "code": issue.code,
                "message": issue.message,
                "occurred_at": issue.occurred_at,
                "raw_record": _raw_row(record, "reviewed"),
                "suggestion": _suggestion_row(db, suggestion) if suggestion else None,
                "resolution": _resolution_row(db, resolution) if resolution else None,
            }
        )
    return {"items": rows, "total": len(rows)}


def _find_overlapping_resolution(
    db: Session, record_ids: set[UUID]
) -> FinancialReviewResolution | None:
    for link in db.scalars(select(FinancialReviewRecordLink)):
        if link.resolution_id is not None and link.raw_import_record_id in record_ids:
            return db.get(FinancialReviewResolution, link.resolution_id)
    return None


def _find_matching_suggestion(
    db: Session,
    suggestion_type: FinancialSuggestionType,
    record_ids: set[UUID],
) -> FinancialReviewSuggestion | None:
    return next(
        (
            item
            for item in db.scalars(
                select(FinancialReviewSuggestion).where(
                    financial_review_suggestions_table.c.suggestion_type
                    == suggestion_type
                )
            )
            if {link.raw_import_record_id for link in _links(db, suggestion_id=item.id)}
            == record_ids
        ),
        None,
    )


@router.post("/reviews/{issue_id}/resolve")
def resolve_financial_review(
    issue_id: UUID, data: ResolutionInput, db: Db
) -> dict[str, Any]:
    issue = db.get(TransformationIssue, issue_id)
    if issue is None:
        raise HTTPException(404, detail={"code": "financial_review_not_found"})
    unique_ids = set(data.record_ids)
    if (
        len(unique_ids) != len(data.record_ids)
        or issue.raw_import_record_id not in unique_ids
    ):
        raise HTTPException(422, detail={"code": "financial_review_records_invalid"})
    records = [db.get(RawImportRecord, item_id) for item_id in data.record_ids]
    if any(item is None for item in records):
        raise HTTPException(422, detail={"code": "financial_review_records_invalid"})
    concrete_records = [item for item in records if item is not None]
    reviewed_ids = {
        item.raw_import_record_id for item in db.scalars(select(TransformationIssue))
    }
    if not unique_ids <= reviewed_ids:
        raise HTTPException(422, detail={"code": "financial_review_records_invalid"})
    suggestion_type = (
        FinancialSuggestionType.OWN_ACCOUNT_FIAT_WITHDRAWAL
        if data.resolution_type is FinancialReviewType.OWN_ACCOUNT_FIAT_WITHDRAWAL
        else FinancialSuggestionType.POSSIBLE_DELISTING_LIQUIDATION
    )
    suggestion = _find_matching_suggestion(db, suggestion_type, unique_ids)
    rejected_suggestion = next(
        (
            item
            for item in db.scalars(select(FinancialReviewSuggestion))
            if item.status is SuggestionStatus.REJECTED
            and any(
                link.raw_import_record_id in unique_ids
                for link in _links(db, suggestion_id=item.id)
            )
        ),
        None,
    )
    overlap = _find_overlapping_resolution(db, unique_ids)
    if overlap is not None:
        linked_ids = {
            item.raw_import_record_id for item in _links(db, resolution_id=overlap.id)
        }
        if (
            data.status == "confirmed"
            and overlap.resolution_type is data.resolution_type
            and linked_ids == unique_ids
            and overlap.decided_by == data.decided_by
            and overlap.reason == data.reason
        ):
            return {"duplicate": True, "resolution": _resolution_row(db, overlap)}
        raise _conflict(
            "financial_review_resolution_conflict",
            "Mindestens ein Record besitzt bereits eine widersprüchliche Entscheidung.",
        )
    if rejected_suggestion is not None:
        if (
            data.status == "rejected"
            and rejected_suggestion is suggestion
            and rejected_suggestion.decided_by == data.decided_by
            and rejected_suggestion.decision_reason == data.reason
        ):
            return {
                "duplicate": True,
                "suggestion": _suggestion_row(db, rejected_suggestion),
            }
        raise _conflict(
            "financial_review_suggestion_already_rejected",
            "Der Vorschlag wurde bereits abgelehnt.",
        )
    if data.status == "rejected":
        if suggestion is None:
            raise HTTPException(
                422, detail={"code": "financial_review_suggestion_missing"}
            )
        suggestion.status = SuggestionStatus.REJECTED
        suggestion.decided_at = utc_now()
        suggestion.decided_by = data.decided_by
        suggestion.decision_reason = data.reason
        db.add(
            AuditEvent(
                occurred_at=suggestion.decided_at,
                event_type="financial_review.suggestion_rejected",
                entity_type="FinancialReviewSuggestion",
                entity_id=suggestion.id,
                actor_type=AuditActorType.USER,
                actor_id=data.decided_by,
                metadata={
                    "reason": data.reason,
                    "record_ids": [str(x) for x in data.record_ids],
                },
            )
        )
        db.commit()
        return {"duplicate": False, "suggestion": _suggestion_row(db, suggestion)}
    try:
        metadata, tax_status, roles = resolution_metadata(
            data.resolution_type, concrete_records
        )
    except ValueError as error:
        raise HTTPException(
            422,
            detail={
                "code": "financial_review_resolution_invalid",
                "message": str(error),
            },
        ) from error
    now = utc_now()
    resolution = FinancialReviewResolution(
        transformation_issue_id=issue.id,
        resolution_type=data.resolution_type,
        status=ResolutionStatus.CONFIRMED,
        created_at=now,
        decided_at=now,
        decided_by=data.decided_by,
        reason=data.reason,
        source="USER_CONFIRMED",
        confidence=None,
        tax_mapping_status=tax_status,
        metadata=metadata,
    )
    db.add(resolution)
    for record, role in roles:
        db.add(
            FinancialReviewRecordLink(
                resolution_id=resolution.id,
                raw_import_record_id=record.id,
                role=role,
            )
        )
    db.add(
        AuditEvent(
            occurred_at=now,
            event_type="financial_review.resolution_confirmed",
            entity_type="FinancialReviewResolution",
            entity_id=resolution.id,
            actor_type=AuditActorType.USER,
            actor_id=data.decided_by,
            metadata={
                "resolution_type": data.resolution_type.value,
                "record_ids": [str(item.id) for item in concrete_records],
                "external_ids": [item.external_id for item in concrete_records],
                "provider_refids": [
                    ledger_values(item).get("refid") for item in concrete_records
                ],
                "reason": data.reason,
                "tax_mapping_status": tax_status.value,
            },
        )
    )
    db.commit()
    return {"duplicate": False, "resolution": _resolution_row(db, resolution)}
