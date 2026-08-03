from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.core.entities import positive_decimal, required_text
from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now


class TransformationStatus(StrEnum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_REVIEW = "completed_with_review"
    FAILED = "failed"


class DecisionType(StrEnum):
    DOMAIN_EVENT_CREATED = "domain_event_created"
    DOMAIN_EVENT_REUSED = "domain_event_reused"
    INTERNAL_MOVEMENT = "internal_movement"
    REVIEW_REQUIRED = "review_required"
    UNSUPPORTED = "unsupported"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class MappingStatus(StrEnum):
    MAPPED = "mapped"
    UNRESOLVED = "unresolved"


class TaxTreatmentHint(StrEnum):
    PASSIVE_STAKING_REWARD = "passive_staking_reward"
    LEGACY_STAKING_REWARD = "legacy_staking_reward"
    TRADE_ACQUISITION = "trade_acquisition"
    TRADE_DISPOSAL = "trade_disposal"
    CRYPTO_ASSET_EXCHANGE = "crypto_asset_exchange"
    CRYPTO_FEE_CANDIDATE = "crypto_fee_candidate"


class AcquisitionType(StrEnum):
    TRADE_BUY = "trade_buy"
    CRYPTO_EXCHANGE = "crypto_exchange"
    STAKING_REWARD = "staking_reward"
    LEGACY_STAKING_REWARD = "legacy_staking_reward"


class DisposalType(StrEnum):
    TRADE_SELL = "trade_sell"
    CRYPTO_EXCHANGE = "crypto_exchange"
    CRYPTO_FEE = "crypto_fee"


class ValuationStatus(StrEnum):
    VALUATION_REQUIRED = "valuation_required"
    NATIVE_EUR_AVAILABLE = "native_eur_available"


class ValuationMethod(StrEnum):
    DAILY_AVERAGE = "daily_average"
    DIRECT_EUR = "direct_eur"


class ReconciliationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    MATCHED = "matched"
    PARTIAL = "partial"
    CONFLICT = "conflict"


def non_negative_decimal(value: Decimal, field_name: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise TypeError(f"{field_name} must be a Decimal.")
    if value < Decimal("0"):
        raise ValueError(f"{field_name} must not be negative.")
    return value


@dataclass(frozen=True, kw_only=True)
class AssetIdentity:
    raw_code: str
    canonical_code: str | None
    mapping_version: str
    mapping_status: MappingStatus
    review_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw_code", required_text(self.raw_code, "raw_code"))
        object.__setattr__(
            self,
            "mapping_version",
            required_text(self.mapping_version, "mapping_version"),
        )
        if self.canonical_code is not None:
            object.__setattr__(
                self,
                "canonical_code",
                required_text(self.canonical_code, "canonical_code").upper(),
            )


@dataclass(kw_only=True)
class TransformationRun:
    contract_version: str
    status: TransformationStatus
    started_at: datetime
    actor_id: str
    id: UUID = field(default_factory=new_id)
    completed_at: datetime | None = None
    checked_records: int = 0
    created_objects: int = 0
    internal_movements: int = 0
    review_cases: int = 0
    error_count: int = 0
    error_summary: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        self.contract_version = required_text(self.contract_version, "contract_version")
        self.actor_id = required_text(self.actor_id, "actor_id")
        self.started_at = require_utc(self.started_at)
        self.created_at = require_utc(self.created_at)
        if self.completed_at is not None:
            self.completed_at = require_utc(self.completed_at)
        for name in (
            "checked_records",
            "created_objects",
            "internal_movements",
            "review_cases",
            "error_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative.")


@dataclass(kw_only=True)
class TransformationRunSession:
    transformation_run_id: UUID
    import_session_id: UUID
    id: UUID = field(default_factory=new_id)


@dataclass(kw_only=True)
class TransformationDecision:
    raw_import_record_id: UUID
    import_session_id: UUID
    transformation_run_id: UUID
    contract_version: str
    decision_type: DecisionType
    reason_code: str
    explanation: str
    decided_at: datetime
    domain_object_id: UUID | None = None
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "contract_version",
            required_text(self.contract_version, "contract_version"),
        )
        object.__setattr__(
            self, "reason_code", required_text(self.reason_code, "reason_code")
        )
        object.__setattr__(
            self, "explanation", required_text(self.explanation, "explanation")
        )
        object.__setattr__(self, "decided_at", require_utc(self.decided_at))


@dataclass(kw_only=True)
class DomainProvenance:
    domain_object_type: str
    domain_object_id: UUID
    raw_import_record_id: UUID
    import_session_id: UUID
    transformation_run_id: UUID
    id: UUID = field(default_factory=new_id)


@dataclass(kw_only=True)
class TransformationIssue:
    transformation_run_id: UUID
    raw_import_record_id: UUID
    code: str
    message: str
    is_conflict: bool
    occurred_at: datetime
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        object.__setattr__(self, "code", required_text(self.code, "code"))
        object.__setattr__(self, "message", required_text(self.message, "message"))
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at))


@dataclass(kw_only=True)
class AcquisitionLot:
    stable_key: str
    payload_hash: str
    asset_raw_code: str
    asset_code: str
    asset_mapping_version: str
    quantity: Decimal
    occurred_at: datetime
    acquisition_type: AcquisitionType
    provider: str
    account_scope: str
    wallet_scope: str
    external_id: str
    transformation_version: str
    valuation_status: ValuationStatus
    tax_treatment_hint: TaxTreatmentHint
    native_consideration_asset: str | None = None
    native_consideration_quantity: Decimal | None = None
    fee_quantity: Decimal = Decimal("0")
    fee_asset: str | None = None
    gross_quantity: Decimal | None = None
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        non_negative_decimal(self.fee_quantity, "fee_quantity")
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at))
        object.__setattr__(self, "created_at", require_utc(self.created_at))


@dataclass(kw_only=True)
class DisposalEvent:
    stable_key: str
    payload_hash: str
    asset_raw_code: str
    asset_code: str
    asset_mapping_version: str
    quantity: Decimal
    occurred_at: datetime
    disposal_type: DisposalType
    provider: str
    account_scope: str
    wallet_scope: str
    external_id: str
    transformation_version: str
    valuation_status: ValuationStatus
    tax_treatment_hint: TaxTreatmentHint
    native_consideration_asset: str | None = None
    native_consideration_quantity: Decimal | None = None
    fee_quantity: Decimal = Decimal("0")
    fee_asset: str | None = None
    fifo_status: str = "open"
    trade_execution_id: UUID | None = None
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        non_negative_decimal(self.fee_quantity, "fee_quantity")
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at))
        object.__setattr__(self, "created_at", require_utc(self.created_at))


@dataclass(kw_only=True)
class TradeExecution:
    stable_key: str
    payload_hash: str
    external_id: str
    order_external_id: str
    raw_pair: str
    base_asset_raw: str
    base_asset: str
    quote_asset_raw: str
    quote_asset: str
    side: str
    order_type: str
    occurred_at: datetime
    volume: Decimal
    price: Decimal
    cost: Decimal
    fee: Decimal
    fee_asset: str | None
    provider: str
    transformation_version: str
    reconciliation_status: ReconciliationStatus
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        positive_decimal(self.volume, "volume")
        positive_decimal(self.price, "price")
        positive_decimal(self.cost, "cost")
        non_negative_decimal(self.fee, "fee")
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at))
        object.__setattr__(self, "created_at", require_utc(self.created_at))


@dataclass(kw_only=True)
class FeeEvent:
    stable_key: str
    payload_hash: str
    asset_code: str
    quantity: Decimal
    occurred_at: datetime
    provider: str
    external_id: str
    transformation_version: str
    valuation_status: ValuationStatus
    related_object_id: UUID
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        object.__setattr__(self, "occurred_at", require_utc(self.occurred_at))
        object.__setattr__(self, "created_at", require_utc(self.created_at))


@dataclass(kw_only=True)
class ValuationRequirement:
    asset_code: str
    target_currency: str
    valuation_at: datetime
    method: ValuationMethod
    status: ValuationStatus
    reason_code: str
    domain_object_type: str
    domain_object_id: UUID
    transformation_run_id: UUID
    id: UUID = field(default_factory=new_id)
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "valuation_at", require_utc(self.valuation_at))
        object.__setattr__(self, "created_at", require_utc(self.created_at))
