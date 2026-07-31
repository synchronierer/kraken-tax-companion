from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID

from app.core.entities import positive_decimal, required_text
from app.core.identifiers import new_id
from app.core.time import require_utc

METHOD_VERSION = "eur-valuation-v1"
MINIMUM_HOURLY_SAMPLES = 20


class PriceMethod(StrEnum):
    NATIVE_EUR = "native_eur"
    DAILY_AVERAGE_HOURLY = "daily_average_hourly"
    MANUAL_DAILY_PRICE = "manual_daily_price"


class ValuationDecisionStatus(StrEnum):
    PENDING = "pending"
    FETCHING = "fetching"
    RESOLVED = "resolved"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class ValuationRunStatus(StrEnum):
    CREATED = "created"
    FETCHING = "fetching"
    APPLYING = "applying"
    COMPLETED = "completed"
    COMPLETED_WITH_REVIEW = "completed_with_review"
    FAILED = "failed"


VALUATION_RUN_TRANSITIONS: dict[ValuationRunStatus, frozenset[ValuationRunStatus]] = {
    ValuationRunStatus.CREATED: frozenset(
        {ValuationRunStatus.FETCHING, ValuationRunStatus.FAILED}
    ),
    ValuationRunStatus.FETCHING: frozenset(
        {ValuationRunStatus.APPLYING, ValuationRunStatus.FAILED}
    ),
    ValuationRunStatus.APPLYING: frozenset(
        {
            ValuationRunStatus.COMPLETED,
            ValuationRunStatus.COMPLETED_WITH_REVIEW,
            ValuationRunStatus.FAILED,
        }
    ),
    ValuationRunStatus.COMPLETED: frozenset(),
    ValuationRunStatus.COMPLETED_WITH_REVIEW: frozenset(),
    ValuationRunStatus.FAILED: frozenset(),
}


class PriceProviderError(RuntimeError):
    def __init__(self, code: str, message: str, *, temporary: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.temporary = temporary


@dataclass(frozen=True, kw_only=True)
class PriceObservation:
    observed_at: datetime
    price_eur: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_at", require_utc(self.observed_at))
        positive_decimal(self.price_eur, "price_eur")


class HistoricalPriceProvider(Protocol):
    name: str
    contract_version: str

    def observations(
        self, asset: str, target_currency: str, start: datetime, end: datetime
    ) -> Sequence[PriceObservation]: ...


@dataclass(kw_only=True)
class ValuationRun:
    provider: str
    correlation_id: UUID
    started_at: datetime
    status: ValuationRunStatus = ValuationRunStatus.CREATED
    contract_version: str = "valuation-run-v1"
    method_version: str = METHOD_VERSION
    id: UUID = field(default_factory=new_id)
    ended_at: datetime | None = None
    checked_requirements: int = 0
    resolved_requirements: int = 0
    native_count: int = 0
    automatic_count: int = 0
    manual_count: int = 0
    review_count: int = 0
    error_count: int = 0
    error_summary: str | None = None

    def __post_init__(self) -> None:
        self.provider = required_text(self.provider, "provider")
        self.started_at = require_utc(self.started_at)
        if self.ended_at is not None:
            self.ended_at = require_utc(self.ended_at)


@dataclass(kw_only=True)
class ProviderEvidence:
    provider: str
    provider_contract_version: str
    provider_asset_id: str
    target_currency: str
    requested_from: datetime
    requested_to: datetime
    fetched_at: datetime
    http_status: int
    response_hash: str
    observation_count: int
    observations: list[dict[str, Any]]
    id: UUID = field(default_factory=new_id)
    earliest_observed_at: datetime | None = None
    latest_observed_at: datetime | None = None

    def __post_init__(self) -> None:
        self.provider = required_text(self.provider, "provider")
        self.provider_asset_id = required_text(
            self.provider_asset_id, "provider_asset_id"
        )
        self.target_currency = required_text(
            self.target_currency, "target_currency"
        ).upper()
        self.requested_from = require_utc(self.requested_from)
        self.requested_to = require_utc(self.requested_to)
        self.fetched_at = require_utc(self.fetched_at)
        if self.requested_to <= self.requested_from or self.http_status < 100:
            raise ValueError("Provider evidence interval or status is invalid.")
        if self.observation_count != len(self.observations):
            raise ValueError("Provider evidence observation count differs.")


@dataclass(kw_only=True)
class DailyPrice:
    asset_code: str
    price_date: date
    unit_price_eur: Decimal
    method: PriceMethod
    source: str
    provider: str
    provider_contract_version: str
    evidence_hash: str
    sample_count: int
    fetched_at: datetime
    status: ValuationDecisionStatus
    version: int = 1
    id: UUID = field(default_factory=new_id)
    earliest_sample_at: datetime | None = None
    latest_sample_at: datetime | None = None
    minimum_price_eur: Decimal | None = None
    maximum_price_eur: Decimal | None = None
    external_reference: str | None = None
    reason: str | None = None
    entered_by: str | None = None
    supersedes_id: UUID | None = None
    provider_evidence_id: UUID | None = None

    def __post_init__(self) -> None:
        self.asset_code = required_text(self.asset_code, "asset_code").upper()
        positive_decimal(self.unit_price_eur, "unit_price_eur")
        self.source = required_text(self.source, "source")
        self.provider = required_text(self.provider, "provider")
        self.fetched_at = require_utc(self.fetched_at)
        if self.sample_count < 0 or self.version < 1:
            raise ValueError("sample_count and version must be valid.")


@dataclass(kw_only=True)
class ValuationDecision:
    valuation_requirement_id: UUID
    valuation_run_id: UUID
    domain_object_type: str
    domain_object_id: UUID
    asset_code: str
    quantity: Decimal
    valuation_at: datetime
    price_date: date
    method: PriceMethod
    unit_price_eur: Decimal
    eur_value: Decimal
    price_source: str
    provider: str
    provider_object_id: UUID | None
    provider_contract_version: str
    method_version: str
    sample_count: int
    fetched_at: datetime
    decided_at: datetime
    status: ValuationDecisionStatus
    reason_code: str
    version: int = 1
    rounding_rule: str = "ROUND_HALF_UP_DISPLAY_ONLY"
    id: UUID = field(default_factory=new_id)
    supersedes_id: UUID | None = None
    provider_evidence_id: UUID | None = None

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        positive_decimal(self.unit_price_eur, "unit_price_eur")
        positive_decimal(self.eur_value, "eur_value")
        self.valuation_at = require_utc(self.valuation_at)
        self.fetched_at = require_utc(self.fetched_at)
        self.decided_at = require_utc(self.decided_at)


def transition_valuation_run(
    run: ValuationRun,
    target: ValuationRunStatus,
    occurred_at: datetime,
    *,
    error_summary: str | None = None,
) -> None:
    if target not in VALUATION_RUN_TRANSITIONS[run.status]:
        raise ValueError(
            f"Valuation transition {run.status.value} -> {target.value} is not allowed."
        )
    occurred_at = require_utc(occurred_at)
    run.status = target
    if target in {
        ValuationRunStatus.COMPLETED,
        ValuationRunStatus.COMPLETED_WITH_REVIEW,
        ValuationRunStatus.FAILED,
    }:
        run.ended_at = occurred_at
    if target is ValuationRunStatus.FAILED:
        run.error_count += 1
        run.error_summary = required_text(error_summary or "", "error_summary")


def utc_day_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value, time.min, UTC)
    return start, datetime.combine(value, time.max, UTC)


def daily_average(
    observations: Sequence[PriceObservation], price_date: date, *, now: datetime
) -> tuple[Decimal, ValuationDecisionStatus, str]:
    if price_date >= require_utc(now).date():
        raise PriceProviderError(
            "valuation_future_date",
            "Der aktuelle oder ein zukünftiger UTC-Tag ist nicht abgeschlossen.",
        )
    start, end = utc_day_bounds(price_date)
    unique = {item.observed_at: item for item in observations}
    valid = sorted(
        (item for item in unique.values() if start <= item.observed_at <= end),
        key=lambda item: item.observed_at,
    )
    if not valid:
        raise PriceProviderError(
            "valuation_no_price_data", "Keine historischen Kursdaten vorhanden."
        )
    average = sum((item.price_eur for item in valid), Decimal("0")) / Decimal(
        len(valid)
    )
    if len(valid) < MINIMUM_HOURLY_SAMPLES:
        return (
            average,
            ValuationDecisionStatus.REVIEW_REQUIRED,
            "valuation_incomplete_daily_coverage",
        )
    return average, ValuationDecisionStatus.RESOLVED, "valuation_resolved"


def evidence_hash(observations: Sequence[PriceObservation]) -> str:
    canonical = "\n".join(
        f"{item.observed_at.isoformat()}|{item.price_eur}"
        for item in sorted(observations, key=lambda item: item.observed_at)
    )
    return sha256(canonical.encode()).hexdigest()


def calculate_eur_value(quantity: Decimal, unit_price: Decimal) -> Decimal:
    positive_decimal(quantity, "quantity")
    positive_decimal(unit_price, "unit_price")
    return quantity * unit_price


def display_cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
