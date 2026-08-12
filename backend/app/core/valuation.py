from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID

from app.core.entities import positive_decimal, required_text
from app.core.identifiers import new_id
from app.core.time import require_utc

LEGACY_METHOD_VERSION = "eur-valuation-v1"
METHOD_VERSION = "eur-valuation-v2"
MINIMUM_HOURLY_SAMPLES = 20
ROUNDING_RULE = "ROUND_HALF_UP_DISPLAY_ONLY"


class FeeTaxClassification(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    WERBUNGSKOSTEN_CANDIDATE = "werbungskosten_candidate"


class FeeTaxReviewStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    REVIEW_REQUIRED = "review_required"


class RewardValuationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, kw_only=True)
class RewardValuationComponents:
    gross_quantity: Decimal | None
    fee_quantity: Decimal | None
    net_quantity: Decimal
    gross_income_eur: Decimal | None
    fee_value_eur: Decimal | None
    net_acquisition_value_eur: Decimal
    valuation_basis: str
    fee_tax_classification: FeeTaxClassification
    fee_tax_review_status: FeeTaxReviewStatus


def _finite_decimal_parts(value: Decimal) -> tuple[int, int]:
    parts = value.as_tuple()
    if not isinstance(parts.exponent, int):
        raise ValueError("Exact arithmetic requires finite Decimal operands.")
    return len(parts.digits), parts.exponent


def exact_decimal_multiply(left: Decimal, right: Decimal) -> Decimal:
    """Multiply finite decimals without context rounding."""

    left_digits, _ = _finite_decimal_parts(left)
    right_digits, _ = _finite_decimal_parts(right)
    with localcontext() as context:
        context.prec = max(1, left_digits + right_digits)
        return left * right


def exact_decimal_sum(values: Sequence[Decimal]) -> Decimal:
    """Sum finite decimals exactly, including widely different exponents."""

    items = tuple(values)
    if not items:
        return Decimal("0")
    parts = tuple(_finite_decimal_parts(value) for value in items)
    minimum_exponent = min(exponent for _, exponent in parts)
    aligned_digits = max(
        digits + exponent - minimum_exponent for digits, exponent in parts
    )
    carry_digits = len(str(len(items)))
    with localcontext() as context:
        context.prec = max(1, aligned_digits + carry_digits)
        return sum(items, Decimal("0"))


def exact_decimal_subtract(left: Decimal, right: Decimal) -> Decimal:
    """Subtract finite decimals without applying the ambient context."""

    return exact_decimal_sum((left, right.copy_negate()))


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
    rounding_rule: str = ROUNDING_RULE
    id: UUID = field(default_factory=new_id)
    supersedes_id: UUID | None = None
    provider_evidence_id: UUID | None = None
    gross_quantity: Decimal | None = None
    fee_quantity: Decimal | None = None
    net_quantity: Decimal | None = None
    gross_income_eur: Decimal | None = None
    fee_value_eur: Decimal | None = None
    net_acquisition_value_eur: Decimal | None = None
    valuation_basis: str | None = None
    fee_tax_classification: FeeTaxClassification | None = None
    fee_tax_review_status: FeeTaxReviewStatus | None = None

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        positive_decimal(self.unit_price_eur, "unit_price_eur")
        positive_decimal(self.eur_value, "eur_value")
        if self.net_quantity is not None:
            positive_decimal(self.net_quantity, "net_quantity")
            if self.net_quantity != self.quantity:
                raise ValueError("net_quantity must equal quantity.")
        if self.gross_quantity is not None:
            positive_decimal(self.gross_quantity, "gross_quantity")
        if self.fee_quantity is not None and self.fee_quantity < 0:
            raise ValueError("fee_quantity must not be negative.")
        if self.gross_income_eur is not None:
            positive_decimal(self.gross_income_eur, "gross_income_eur")
        if self.fee_value_eur is not None and self.fee_value_eur < 0:
            raise ValueError("fee_value_eur must not be negative.")
        if self.net_acquisition_value_eur is not None:
            positive_decimal(
                self.net_acquisition_value_eur, "net_acquisition_value_eur"
            )
            if self.net_acquisition_value_eur != self.eur_value:
                raise ValueError("eur_value must equal net_acquisition_value_eur.")
        reward_values = (
            self.gross_quantity,
            self.fee_quantity,
            self.gross_income_eur,
            self.fee_value_eur,
        )
        if any(value is not None for value in reward_values) and any(
            value is None for value in reward_values
        ):
            raise ValueError("Reward components must be complete.")
        if (
            self.gross_quantity is not None
            and self.fee_quantity is not None
            and (
                self.net_quantity is None
                or self.gross_quantity
                != exact_decimal_sum((self.net_quantity, self.fee_quantity))
            )
        ):
            raise ValueError("Reward quantities are inconsistent.")
        if (
            self.gross_income_eur is not None
            and self.fee_value_eur is not None
            and (
                self.net_acquisition_value_eur is None
                or self.gross_income_eur
                != exact_decimal_sum(
                    (self.net_acquisition_value_eur, self.fee_value_eur)
                )
            )
        ):
            raise ValueError("Reward EUR components are inconsistent.")
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


def calculate_reward_valuation(
    *,
    net_quantity: Decimal,
    gross_quantity: Decimal | None,
    fee_quantity: Decimal | None,
    asset_code: str,
    fee_asset: str | None,
    unit_price_eur: Decimal,
    method_version: str,
) -> RewardValuationComponents:
    positive_decimal(net_quantity, "net_quantity")
    positive_decimal(unit_price_eur, "unit_price_eur")
    if method_version == LEGACY_METHOD_VERSION:
        return RewardValuationComponents(
            gross_quantity=None,
            fee_quantity=None,
            net_quantity=net_quantity,
            gross_income_eur=None,
            fee_value_eur=None,
            net_acquisition_value_eur=net_quantity * unit_price_eur,
            valuation_basis="staking_reward_net_quantity_legacy_v1",
            fee_tax_classification=FeeTaxClassification.NOT_APPLICABLE,
            fee_tax_review_status=FeeTaxReviewStatus.NOT_REQUIRED,
        )
    effective_gross = gross_quantity
    effective_fee = fee_quantity
    basis = "staking_reward_components_v2"
    if effective_gross is None:
        if effective_fee not in {None, Decimal("0")}:
            raise RewardValuationError(
                "valuation_reward_quantity_inconsistent",
                "Eine Gebührenmenge ohne Bruttomenge ist nicht eindeutig.",
            )
        effective_gross = net_quantity
        effective_fee = Decimal("0")
        basis = "staking_reward_legacy_lot_fallback_v2"
    if effective_fee is None:
        raise RewardValuationError(
            "valuation_reward_quantity_inconsistent",
            "Die Gebührenmenge des Staking-Rewards fehlt.",
        )
    if effective_gross <= 0 or effective_fee < 0:
        raise RewardValuationError(
            "valuation_reward_quantity_inconsistent",
            "Die Mengen des Staking-Rewards sind ungültig.",
        )
    if effective_fee > effective_gross or effective_gross != exact_decimal_sum(
        (net_quantity, effective_fee)
    ):
        raise RewardValuationError(
            "valuation_reward_quantity_inconsistent",
            "Brutto-, Gebühren- und Nettomenge des Staking-Rewards widersprechen sich.",
        )
    if effective_fee and fee_asset != asset_code:
        raise RewardValuationError(
            "valuation_reward_fee_asset_mismatch",
            "Reward und einbehaltene Gebühr verwenden unterschiedliche Assets.",
        )
    gross_income = exact_decimal_multiply(effective_gross, unit_price_eur)
    fee_value = exact_decimal_multiply(effective_fee, unit_price_eur)
    net_value = exact_decimal_multiply(net_quantity, unit_price_eur)
    has_fee = effective_fee > 0
    return RewardValuationComponents(
        gross_quantity=effective_gross,
        fee_quantity=effective_fee,
        net_quantity=net_quantity,
        gross_income_eur=gross_income,
        fee_value_eur=fee_value,
        net_acquisition_value_eur=net_value,
        valuation_basis=basis,
        fee_tax_classification=(
            FeeTaxClassification.WERBUNGSKOSTEN_CANDIDATE
            if has_fee
            else FeeTaxClassification.NOT_APPLICABLE
        ),
        fee_tax_review_status=(
            FeeTaxReviewStatus.REVIEW_REQUIRED
            if has_fee
            else FeeTaxReviewStatus.NOT_REQUIRED
        ),
    )


def display_cents(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
