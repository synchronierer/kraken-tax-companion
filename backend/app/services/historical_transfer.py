"""Offline, evidence-driven historical transfer resolution.

The service never infers a basis from a Kraken deposit alone. It persists the
evidence link first and only projects complete source acquisitions and
explicit source fees.
"""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

from app.adapters.kraken.assets import resolve_asset
from app.core.historical_transfer import (
    HistoricalBasisCoverage,
    HistoricalResolutionStatus,
    HistoricalTransferLink,
    HistoricalTransferNature,
    HistoricalTransferResolution,
)
from app.core.time import utc_now
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    DomainProvenance,
    FeeEvent,
    TaxTreatmentHint,
    TransformationIssue,
    TransformationRun,
    TransformationStatus,
    ValuationStatus,
)
from app.core.unit_of_work import UnitOfWork
from app.imports.hashing import canonical_sha256

HISTORICAL_TRANSFER_VERSION = "historical-transfer-v1"
EXTERNAL_ACCOUNT_SCOPE = "external"
EXTERNAL_WALLET_SCOPE = "external"


def _decimal(payload: dict[str, Any], key: str) -> Decimal | None:
    value = payload.get(key, "")
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _timestamp(payload: dict[str, Any]) -> datetime | None:
    value = payload.get("occurred_at", "")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _hash_payload(values: dict[str, Any]) -> str:
    return canonical_sha256({str(key): str(value) for key, value in values.items()})


class HistoricalTransferService:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], UnitOfWork],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def resolve(
        self,
        *,
        source_record_ids: Sequence[UUID],
        canonical_asset: str,
        source_quantity: Decimal,
        target_quantity: Decimal,
        transfer_nature: HistoricalTransferNature,
        basis_coverage: HistoricalBasisCoverage,
        evidence_type: str,
        target_raw_import_record_id: UUID | None = None,
        target_fingerprint: str | None = None,
        source_fee_quantity: Decimal = Decimal("0"),
        source_fee_asset: str | None = None,
        parent_link_id: UUID | None = None,
        explanation: str = "Historical source evidence recorded.",
        evidence_level: str = "original_exchange",
        actor_id: str = "historical-reconciliation",
    ) -> HistoricalTransferResolution:
        now = self._clock()
        source_ids = tuple(sorted(str(item) for item in source_record_ids))
        identity = {
            "version": HISTORICAL_TRANSFER_VERSION,
            "source_record_ids": source_ids,
            "target_record_id": str(target_raw_import_record_id or ""),
            "target_fingerprint": target_fingerprint or "",
            "asset": canonical_asset,
            "source_quantity": str(source_quantity),
            "target_quantity": str(target_quantity),
            "nature": transfer_nature.value,
            "evidence_type": evidence_type,
            "source_fee_quantity": str(source_fee_quantity),
            "source_fee_asset": source_fee_asset or "",
        }
        stable_key = f"historical-transfer:{canonical_sha256(identity)}"
        evidence_hash = _hash_payload(identity)
        resolution_values = {
            "stable_key": stable_key,
            "coverage": basis_coverage.value,
            "evidence_level": evidence_level,
            "explanation": explanation,
        }
        resolution_hash = _hash_payload(resolution_values)
        with self._unit_of_work_factory() as unit:
            existing = unit.historical_transfer_links.find_by_stable_key(stable_key)
            if existing is None:
                link = HistoricalTransferLink(
                    stable_key=stable_key,
                    version=HISTORICAL_TRANSFER_VERSION,
                    evidence_type=evidence_type,
                    source_record_ids=source_ids,
                    target_raw_import_record_id=target_raw_import_record_id,
                    target_fingerprint=target_fingerprint,
                    canonical_asset=canonical_asset,
                    source_quantity=source_quantity,
                    target_quantity=target_quantity,
                    transfer_nature=transfer_nature,
                    basis_coverage=basis_coverage,
                    source_fee_quantity=source_fee_quantity,
                    source_fee_asset=source_fee_asset,
                    transport_difference=abs(source_quantity - target_quantity),
                    evidence_hash=evidence_hash,
                    resolution_hash=resolution_hash,
                    parent_link_id=parent_link_id,
                    created_at=now,
                    created_by=actor_id,
                )
                unit.historical_transfer_links.add(link)
            else:
                link = existing
            resolution_key = f"{link.stable_key}:resolution:{resolution_hash}"
            resolution = unit.historical_transfer_resolutions.find_by_stable_key(
                resolution_key
            )
            if resolution is None:
                run = TransformationRun(
                    contract_version=HISTORICAL_TRANSFER_VERSION,
                    status=TransformationStatus.COMPLETED,
                    started_at=now,
                    completed_at=now,
                    actor_id=actor_id,
                    created_at=now,
                )
                unit.transformation_runs.add(run)
                resolution = HistoricalTransferResolution(
                    stable_key=resolution_key,
                    historical_transfer_link_id=link.id,
                    version=HISTORICAL_TRANSFER_VERSION,
                    status=_resolution_status(basis_coverage),
                    basis_coverage=basis_coverage,
                    evidence_level=evidence_level,
                    resolution_hash=resolution_hash,
                    explanation=explanation,
                    created_at=now,
                    created_by=actor_id,
                    resolved_at=(
                        now
                        if basis_coverage is HistoricalBasisCoverage.COMPLETE
                        else None
                    ),
                )
                unit.historical_transfer_resolutions.add(resolution)
                self._project_source_records(
                    unit=unit,
                    run=run,
                    source_record_ids=source_record_ids,
                    link=link,
                    now=now,
                )
                unit.commit()
            else:
                unit.commit()
            return resolution

    def _project_source_records(
        self,
        *,
        unit: UnitOfWork,
        run: TransformationRun,
        source_record_ids: Sequence[str | UUID],
        link: HistoricalTransferLink,
        now: datetime,
    ) -> None:
        for raw_id in source_record_ids:
            try:
                raw_record = unit.raw_imports.get(
                    raw_id if isinstance(raw_id, UUID) else UUID(raw_id)
                )
            except ValueError:
                raw_record = None
            if raw_record is None:
                continue
            payload = {str(key): value for key, value in raw_record.payload.items()}
            record_type = str(payload.get("record_type", "")).lower()
            occurred_at = _timestamp(payload)
            if record_type not in {"buy", "acquisition"}:
                if record_type in {"network_fee", "withdrawal"}:
                    self._project_direct_fee(
                        unit=unit,
                        run=run,
                        raw_record=raw_record,
                        link=link,
                        occurred_at=occurred_at,
                        now=now,
                    )
                continue
            quantity = _decimal(payload, "quantity")
            cost_eur = _decimal(payload, "cost_eur")
            if (
                quantity is None
                or quantity <= 0
                or cost_eur is None
                or cost_eur <= 0
                or occurred_at is None
            ):
                self._gap(unit, run, raw_record.id, link, quantity, now)
                continue
            try:
                asset = resolve_asset(str(payload.get("asset", "")))
            except ValueError:
                self._gap(unit, run, raw_record.id, link, quantity, now)
                continue
            if asset.canonical_code is None:
                self._gap(unit, run, raw_record.id, link, quantity, now)
                continue
            stable_key = (
                f"historical-acquisition:{raw_record.external_id or raw_record.id}"
            )
            lot = AcquisitionLot(
                stable_key=stable_key,
                payload_hash=raw_record.content_hash,
                asset_raw_code=asset.raw_code,
                asset_code=asset.canonical_code,
                asset_mapping_version=asset.mapping_version,
                quantity=quantity,
                occurred_at=occurred_at,
                acquisition_type=AcquisitionType.HISTORICAL_EXTERNAL,
                provider=raw_record.source,
                account_scope=EXTERNAL_ACCOUNT_SCOPE,
                wallet_scope=EXTERNAL_WALLET_SCOPE,
                external_id=raw_record.external_id or str(raw_record.id),
                transformation_version=HISTORICAL_TRANSFER_VERSION,
                valuation_status=ValuationStatus.NATIVE_EUR_AVAILABLE,
                tax_treatment_hint=TaxTreatmentHint.TRADE_ACQUISITION,
                native_consideration_asset="EUR",
                native_consideration_quantity=cost_eur,
                created_at=now,
            )
            existing = unit.acquisitions.find_by_stable_key(stable_key)
            if existing is None:
                unit.acquisitions.add(lot)
                run.created_objects += 1
                unit.domain_provenance.add(
                    DomainProvenance(
                        domain_object_type="AcquisitionLot",
                        domain_object_id=lot.id,
                        raw_import_record_id=raw_record.id,
                        import_session_id=raw_record.import_session_id,
                        transformation_run_id=run.id,
                    )
                )
            fee = _decimal(payload, "fee_quantity")
            fee_asset = str(payload.get("fee_asset", "")).strip().upper()
            if fee is None or fee <= 0 or not fee_asset:
                continue
            self._project_fee(
                unit=unit,
                run=run,
                raw_record=raw_record,
                fee=fee,
                fee_asset=fee_asset,
                occurred_at=occurred_at,
                related_object_id=lot.id if existing is None else existing.id,
                now=now,
            )

    @staticmethod
    def _project_direct_fee(
        *,
        unit: UnitOfWork,
        run: TransformationRun,
        raw_record: Any,
        link: HistoricalTransferLink,
        occurred_at: datetime | None,
        now: datetime,
    ) -> None:
        payload = {str(key): value for key, value in raw_record.payload.items()}
        fee = _decimal(payload, "fee_quantity")
        fee_asset = str(payload.get("fee_asset", "")).strip().upper()
        if fee is None or fee <= 0 or not fee_asset or occurred_at is None:
            return
        HistoricalTransferService._project_fee(
            unit=unit,
            run=run,
            raw_record=raw_record,
            fee=fee,
            fee_asset=fee_asset,
            occurred_at=occurred_at,
            related_object_id=link.id,
            now=now,
        )

    @staticmethod
    def _project_fee(
        *,
        unit: UnitOfWork,
        run: TransformationRun,
        raw_record: Any,
        fee: Decimal,
        fee_asset: str,
        occurred_at: datetime,
        related_object_id: UUID,
        now: datetime,
    ) -> None:
        fee_key = f"historical-fee:{raw_record.external_id or raw_record.id}"
        fee_event = FeeEvent(
            stable_key=fee_key,
            payload_hash=raw_record.content_hash,
            asset_code=fee_asset,
            quantity=fee,
            occurred_at=occurred_at,
            provider=raw_record.source,
            external_id=raw_record.external_id or str(raw_record.id),
            transformation_version=HISTORICAL_TRANSFER_VERSION,
            valuation_status=ValuationStatus.VALUATION_REQUIRED,
            related_object_id=related_object_id,
            created_at=now,
        )
        if unit.fee_events.find_by_stable_key(fee_key) is None:
            unit.fee_events.add(fee_event)
            run.created_objects += 1
            unit.domain_provenance.add(
                DomainProvenance(
                    domain_object_type="FeeEvent",
                    domain_object_id=fee_event.id,
                    raw_import_record_id=raw_record.id,
                    import_session_id=raw_record.import_session_id,
                    transformation_run_id=run.id,
                )
            )

    @staticmethod
    def _gap(
        unit: UnitOfWork,
        run: TransformationRun,
        raw_record_id: UUID,
        link: HistoricalTransferLink,
        quantity: Decimal | None,
        now: datetime,
        reason: str = "Historical acquisition date or cost basis is incomplete.",
    ) -> None:
        unresolved = (
            quantity if quantity is not None and quantity > 0 else link.target_quantity
        )
        unit.transformation_issues.add(
            TransformationIssue(
                transformation_run_id=run.id,
                raw_import_record_id=raw_record_id,
                code="historical_cost_basis_gap",
                message=(
                    f"asset={link.canonical_asset}; unresolved_quantity={unresolved}; "
                    f"historical_transfer_link_id={link.id}; reason={reason}"
                ),
                is_conflict=False,
                occurred_at=now,
            )
        )


def _resolution_status(
    coverage: HistoricalBasisCoverage,
) -> HistoricalResolutionStatus:
    if coverage is HistoricalBasisCoverage.COMPLETE:
        return HistoricalResolutionStatus.RESOLVED
    if coverage is HistoricalBasisCoverage.PARTIAL:
        return HistoricalResolutionStatus.PARTIAL
    return HistoricalResolutionStatus.REVIEW_REQUIRED
