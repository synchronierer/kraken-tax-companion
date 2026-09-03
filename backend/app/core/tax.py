from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from hashlib import sha256
from typing import Final
from uuid import UUID

from app.core.entities import positive_decimal, required_text
from app.core.identifiers import new_id
from app.core.time import require_utc, utc_now
from app.core.transformation import non_negative_decimal
from app.core.valuation import (
    exact_decimal_multiply,
    exact_decimal_subtract,
    exact_decimal_sum,
)

FIFO_RULE_VERSION: Final = "fifo-utc-stable-v1"
FEE_RULE_VERSION: Final = "proportional-last-remainder-v1"
CLASSIFICATION_RULE_VERSION: Final = "private-assets-reward-fee-decision-v3"
JOURNAL_RULE_VERSION: Final = "tax-journal-reward-fee-decision-v3"
EXPORT_FORMAT_VERSION: Final = "tax-export-review-decisions-v3"
NON_INVENTORY_ASSETS: Final = frozenset({"EUR", "USD"})


class TaxRunStatus(StrEnum):
    CREATED = "created"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_REVIEW = "completed_with_review"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TaxRecordStatus(StrEnum):
    RESOLVED = "resolved"
    REVIEW_REQUIRED = "review_required"
    SUPERSEDED = "superseded"


class TaxReviewDecisionValue(StrEnum):
    INCLUDE_AS_WERBUNGSKOSTEN = "include_as_werbungskosten"
    EXCLUDE_FROM_WERBUNGSKOSTEN = "exclude_from_werbungskosten"


class JournalEntryType(StrEnum):
    ACQUISITION = "acquisition"
    EARN_INFLOW = "earn_inflow"
    DISPOSAL = "disposal"
    EXCHANGE = "exchange"
    FEE = "fee"
    REALIZED_GAIN = "realized_gain"
    REALIZED_LOSS = "realized_loss"
    REVIEW = "review"
    CORRECTION = "correction"


class ExportKind(StrEnum):
    TAX_JOURNAL_CSV = "tax_journal_csv"
    FIFO_ALLOCATIONS_CSV = "fifo_allocations_csv"
    INVENTORY_CSV = "inventory_csv"
    VALUATION_EVIDENCE_CSV = "valuation_evidence_csv"
    REVIEWS_CSV = "reviews_csv"
    ANNUAL_SUMMARY_CSV = "annual_summary_csv"
    TAX_REPORT_PDF = "tax_report_pdf"


class ExportStatus(StrEnum):
    CREATED = "created"
    COMPLETED = "completed"
    FAILED = "failed"


EXPORT_FORMAT_VERSIONS: Final[dict[ExportKind, str]] = {
    ExportKind.TAX_JOURNAL_CSV: "tax-journal-csv-v1",
    ExportKind.FIFO_ALLOCATIONS_CSV: "fifo-allocations-csv-v1",
    ExportKind.INVENTORY_CSV: "inventory-csv-v1",
    ExportKind.VALUATION_EVIDENCE_CSV: "valuation-evidence-csv-v1",
    ExportKind.REVIEWS_CSV: "reviews-csv-v1",
    ExportKind.ANNUAL_SUMMARY_CSV: "annual-summary-csv-v1",
    ExportKind.TAX_REPORT_PDF: "tax-report-pdf-v2",
}


@dataclass(frozen=True, kw_only=True)
class TaxRuleVersion:
    fifo: str = FIFO_RULE_VERSION
    fees: str = FEE_RULE_VERSION
    classification: str = CLASSIFICATION_RULE_VERSION
    journal: str = JOURNAL_RULE_VERSION
    export: str = EXPORT_FORMAT_VERSION

    def __post_init__(self) -> None:
        for name in ("fifo", "fees", "classification", "journal", "export"):
            required_text(getattr(self, name), name)

    @property
    def fingerprint(self) -> str:
        values = (self.fifo, self.fees, self.classification, self.journal, self.export)
        return sha256("\x1f".join(values).encode()).hexdigest()


@dataclass(frozen=True, kw_only=True)
class TaxReportingPeriod:
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.start > self.end:
            raise ValueError("Reporting period start must not be after end.")

    @classmethod
    def for_year(cls, year: int) -> "TaxReportingPeriod":
        if year < 1970 or year > 9999:
            raise ValueError("Tax year is outside the supported range.")
        return cls(start=date(year, 1, 1), end=date(year, 12, 31))


@dataclass(frozen=True, kw_only=True)
class AcquisitionInput:
    acquisition_id: UUID
    asset_code: str
    quantity: Decimal
    acquired_at: datetime
    value_eur: Decimal
    fee_eur: Decimal
    valuation_decision_id: UUID
    acquisition_type: str
    gross_income_eur: Decimal | None = None
    platform_fee_candidate_eur: Decimal = Decimal("0")
    platform_fee_decision: TaxReviewDecisionValue | None = None
    tax_review_decision_id: UUID | None = None
    tax_review_decision_version: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "asset_code", required_text(self.asset_code, "asset").upper()
        )
        positive_decimal(self.quantity, "quantity")
        positive_decimal(self.value_eur, "value_eur")
        non_negative_decimal(self.fee_eur, "fee_eur")
        if self.gross_income_eur is not None:
            positive_decimal(self.gross_income_eur, "gross_income_eur")
        non_negative_decimal(
            self.platform_fee_candidate_eur, "platform_fee_candidate_eur"
        )
        if (self.platform_fee_decision is None) != (
            self.tax_review_decision_id is None
        ):
            raise ValueError("Review decision and its identifier must be complete.")
        if self.tax_review_decision_id is not None and (
            self.tax_review_decision_version is None
            or self.tax_review_decision_version < 1
        ):
            raise ValueError("Review decision version must be positive.")
        object.__setattr__(self, "acquired_at", require_utc(self.acquired_at))
        required_text(self.acquisition_type, "acquisition_type")


@dataclass(frozen=True, kw_only=True)
class DisposalInput:
    disposal_id: UUID
    asset_code: str
    quantity: Decimal
    disposed_at: datetime
    proceeds_eur: Decimal
    fee_eur: Decimal
    valuation_decision_id: UUID
    disposal_type: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "asset_code", required_text(self.asset_code, "asset").upper()
        )
        positive_decimal(self.quantity, "quantity")
        positive_decimal(self.proceeds_eur, "proceeds_eur")
        non_negative_decimal(self.fee_eur, "fee_eur")
        object.__setattr__(self, "disposed_at", require_utc(self.disposed_at))
        required_text(self.disposal_type, "disposal_type")


@dataclass(kw_only=True)
class TaxCalculationRun:
    period_start: date
    period_end: date
    snapshot_hash: str
    rules_fingerprint: str
    status: TaxRunStatus
    started_at: datetime
    fifo_rule_version: str = FIFO_RULE_VERSION
    fee_rule_version: str = FEE_RULE_VERSION
    classification_rule_version: str = CLASSIFICATION_RULE_VERSION
    journal_rule_version: str = JOURNAL_RULE_VERSION
    export_format_version: str = EXPORT_FORMAT_VERSION
    id: UUID = field(default_factory=new_id)
    ended_at: datetime | None = None
    checked_events: int = 0
    created_allocations: int = 0
    created_journal_entries: int = 0
    review_count: int = 0
    error_count: int = 0
    error_summary: str | None = None
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        TaxReportingPeriod(start=self.period_start, end=self.period_end)
        for name in ("snapshot_hash", "rules_fingerprint"):
            value = required_text(getattr(self, name), name).lower()
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{name} must be a SHA-256 digest.")
            setattr(self, name, value)
        self.started_at = require_utc(self.started_at)
        for name in (
            "fifo_rule_version",
            "fee_rule_version",
            "classification_rule_version",
            "journal_rule_version",
            "export_format_version",
        ):
            setattr(self, name, required_text(getattr(self, name), name))
        if self.ended_at is not None:
            self.ended_at = require_utc(self.ended_at)
        for name in (
            "checked_events",
            "created_allocations",
            "created_journal_entries",
            "review_count",
            "error_count",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative.")


@dataclass(kw_only=True)
class InventoryLot:
    tax_calculation_run_id: UUID
    acquisition_lot_id: UUID
    asset_code: str
    original_quantity: Decimal
    remaining_quantity: Decimal
    acquired_at: datetime
    acquisition_value_eur: Decimal
    acquisition_fee_eur: Decimal
    remaining_cost_eur: Decimal
    valuation_decision_id: UUID
    rule_version: str
    sequence: int
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.asset_code = required_text(self.asset_code, "asset_code").upper()
        positive_decimal(self.original_quantity, "original_quantity")
        non_negative_decimal(self.remaining_quantity, "remaining_quantity")
        if self.remaining_quantity > self.original_quantity:
            raise ValueError("remaining_quantity exceeds original_quantity.")
        positive_decimal(self.acquisition_value_eur, "acquisition_value_eur")
        non_negative_decimal(self.acquisition_fee_eur, "acquisition_fee_eur")
        non_negative_decimal(self.remaining_cost_eur, "remaining_cost_eur")
        self.acquired_at = require_utc(self.acquired_at)
        self.rule_version = required_text(self.rule_version, "rule_version")
        if self.sequence < 0:
            raise ValueError("sequence must not be negative.")


@dataclass(kw_only=True)
class LotAllocation:
    tax_calculation_run_id: UUID
    disposal_event_id: UUID
    inventory_lot_id: UUID
    allocated_quantity: Decimal
    allocation_order: int
    acquisition_cost_eur: Decimal
    disposal_proceeds_eur: Decimal
    disposal_fee_eur: Decimal
    gain_loss_eur: Decimal
    acquired_at: datetime
    disposed_at: datetime
    holding_seconds: int
    fifo_rule_version: str
    fee_rule_version: str
    created_at: datetime = field(default_factory=utc_now)
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        positive_decimal(self.allocated_quantity, "allocated_quantity")
        non_negative_decimal(self.acquisition_cost_eur, "acquisition_cost_eur")
        non_negative_decimal(self.disposal_proceeds_eur, "disposal_proceeds_eur")
        non_negative_decimal(self.disposal_fee_eur, "disposal_fee_eur")
        self.acquired_at = require_utc(self.acquired_at)
        self.disposed_at = require_utc(self.disposed_at)
        self.created_at = require_utc(self.created_at)
        if self.allocation_order < 1 or self.holding_seconds < 0:
            raise ValueError("Allocation order and holding time must be non-negative.")


@dataclass(kw_only=True)
class DisposalCalculation:
    tax_calculation_run_id: UUID
    disposal_event_id: UUID
    quantity: Decimal
    allocated_quantity: Decimal
    proceeds_eur: Decimal
    acquisition_cost_eur: Decimal
    fees_eur: Decimal
    gain_loss_eur: Decimal
    status: TaxRecordStatus
    rule_version: str
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        positive_decimal(self.quantity, "quantity")
        non_negative_decimal(self.allocated_quantity, "allocated_quantity")
        if self.allocated_quantity > self.quantity:
            raise ValueError("allocated_quantity exceeds quantity.")
        for name in ("proceeds_eur", "acquisition_cost_eur", "fees_eur"):
            non_negative_decimal(getattr(self, name), name)
        self.rule_version = required_text(self.rule_version, "rule_version")


@dataclass(kw_only=True)
class TaxJournalEntry:
    tax_calculation_run_id: UUID
    occurred_at: datetime
    tax_year: int
    entry_type: JournalEntryType
    asset_code: str
    quantity: Decimal
    eur_value: Decimal
    proceeds_eur: Decimal | None
    acquisition_cost_eur: Decimal | None
    gain_loss_eur: Decimal | None
    holding_seconds: int | None
    classification: str
    rule_version: str
    status: TaxRecordStatus
    source_object_type: str
    source_object_id: UUID
    valuation_decision_id: UUID | None = None
    lot_allocation_id: UUID | None = None
    supersedes_id: UUID | None = None
    tax_review_decision_id: UUID | None = None
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.occurred_at = require_utc(self.occurred_at)
        self.asset_code = required_text(self.asset_code, "asset_code").upper()
        positive_decimal(self.quantity, "quantity")
        non_negative_decimal(self.eur_value, "eur_value")
        for name in ("proceeds_eur", "acquisition_cost_eur"):
            value = getattr(self, name)
            if value is not None:
                non_negative_decimal(value, name)
        if self.holding_seconds is not None and self.holding_seconds < 0:
            raise ValueError("holding_seconds must not be negative.")


@dataclass(kw_only=True)
class TaxReviewCase:
    tax_calculation_run_id: UUID
    code: str
    message: str
    source_object_type: str
    source_object_id: UUID
    occurred_at: datetime
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.code = required_text(self.code, "code")
        self.message = required_text(self.message, "message")
        self.occurred_at = require_utc(self.occurred_at)


@dataclass(kw_only=True)
class TaxReviewDecision:
    valuation_decision_id: UUID
    source_tax_review_case_id: UUID
    decision: TaxReviewDecisionValue
    reason: str
    actor_id: str
    decided_at: datetime
    version: int
    batch_id: UUID
    id: UUID = field(default_factory=new_id)
    supersedes_id: UUID | None = None

    def __post_init__(self) -> None:
        self.reason = required_text(self.reason, "reason")
        self.actor_id = required_text(self.actor_id, "actor_id")
        self.decided_at = require_utc(self.decided_at)
        if self.version < 1:
            raise ValueError("Review decision version must be positive.")
        if (self.version == 1) != (self.supersedes_id is None):
            raise ValueError("Review decision supersedes chain is inconsistent.")


def effective_tax_review_decisions(
    decisions: list[TaxReviewDecision],
) -> dict[UUID, TaxReviewDecision]:
    """Return the effective decision after validating every immutable chain."""

    grouped: dict[UUID, list[TaxReviewDecision]] = {}
    for decision in decisions:
        grouped.setdefault(decision.valuation_decision_id, []).append(decision)
    effective: dict[UUID, TaxReviewDecision] = {}
    for valuation_id, items in grouped.items():
        ordered = sorted(items, key=lambda item: (item.version, item.id.hex))
        if [item.version for item in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("Review decision versions must be consecutive.")
        previous: TaxReviewDecision | None = None
        for item in ordered:
            if item.supersedes_id != (previous.id if previous else None):
                raise ValueError("Review decision supersedes chain is inconsistent.")
            previous = item
        effective[valuation_id] = ordered[-1]
    return effective


@dataclass(kw_only=True)
class ExportRun:
    tax_calculation_run_id: UUID
    kind: ExportKind
    status: ExportStatus
    period_start: date
    period_end: date
    rules_fingerprint: str
    format_version: str
    started_at: datetime
    id: UUID = field(default_factory=new_id)
    completed_at: datetime | None = None
    error_summary: str | None = None

    def __post_init__(self) -> None:
        TaxReportingPeriod(start=self.period_start, end=self.period_end)
        self.rules_fingerprint = required_text(
            self.rules_fingerprint, "rules_fingerprint"
        )
        self.format_version = required_text(self.format_version, "format_version")
        self.started_at = require_utc(self.started_at)
        if self.completed_at is not None:
            self.completed_at = require_utc(self.completed_at)


@dataclass(kw_only=True)
class ExportArtifact:
    export_run_id: UUID
    kind: ExportKind
    file_name: str
    media_type: str
    size_bytes: int
    sha256_hash: str
    created_at: datetime
    id: UUID = field(default_factory=new_id)

    def __post_init__(self) -> None:
        self.file_name = required_text(self.file_name, "file_name")
        if (
            "/" in self.file_name
            or "\\" in self.file_name
            or self.file_name in {".", ".."}
        ):
            raise ValueError("file_name must be a safe base name.")
        self.media_type = required_text(self.media_type, "media_type")
        if self.size_bytes < 0:
            raise ValueError("size_bytes must not be negative.")
        self.created_at = require_utc(self.created_at)


@dataclass(frozen=True, kw_only=True)
class FifoResult:
    lots: tuple[InventoryLot, ...]
    allocations: tuple[LotAllocation, ...]
    calculations: tuple[DisposalCalculation, ...]
    journal: tuple[TaxJournalEntry, ...]
    reviews: tuple[TaxReviewCase, ...]


def tax_snapshot_hash(
    acquisitions: list[AcquisitionInput], disposals: list[DisposalInput]
) -> str:
    rows = [
        f"A|{item.acquisition_id}|{item.valuation_decision_id}|{item.quantity}|"
        f"{item.value_eur}|{item.fee_eur}|{item.acquisition_type}|"
        f"{item.gross_income_eur}|{item.platform_fee_candidate_eur}|"
        f"{item.tax_review_decision_id}|{item.tax_review_decision_version}|"
        f"{item.platform_fee_decision}"
        for item in sorted(
            acquisitions, key=lambda item: (item.acquired_at, item.acquisition_id.hex)
        )
    ]
    rows.extend(
        f"D|{item.disposal_id}|{item.valuation_decision_id}|{item.quantity}|"
        f"{item.proceeds_eur}|{item.fee_eur}|{item.disposal_type}"
        for item in sorted(
            disposals, key=lambda item: (item.disposed_at, item.disposal_id.hex)
        )
    )
    return sha256("\n".join(rows).encode()).hexdigest()


def _share(total: Decimal, quantity: Decimal, full_quantity: Decimal) -> Decimal:
    exact_numerator = exact_decimal_multiply(total, quantity)
    with localcontext():
        return exact_numerator / full_quantity


def calculate_fifo(
    *,
    run_id: UUID,
    period: TaxReportingPeriod,
    rules: TaxRuleVersion,
    acquisitions: list[AcquisitionInput],
    disposals: list[DisposalInput],
) -> FifoResult:
    ordered_acquisitions = sorted(
        (
            item
            for item in acquisitions
            if item.acquired_at.date() <= period.end
            and item.asset_code not in NON_INVENTORY_ASSETS
        ),
        key=lambda item: (item.acquired_at, item.acquisition_id.hex),
    )
    lots = [
        InventoryLot(
            tax_calculation_run_id=run_id,
            acquisition_lot_id=item.acquisition_id,
            asset_code=item.asset_code,
            original_quantity=item.quantity,
            remaining_quantity=item.quantity,
            acquired_at=item.acquired_at,
            acquisition_value_eur=item.value_eur,
            acquisition_fee_eur=item.fee_eur,
            remaining_cost_eur=exact_decimal_sum((item.value_eur, item.fee_eur)),
            valuation_decision_id=item.valuation_decision_id,
            rule_version=rules.fifo,
            sequence=index,
        )
        for index, item in enumerate(ordered_acquisitions)
    ]
    journal = [
        TaxJournalEntry(
            tax_calculation_run_id=run_id,
            occurred_at=item.acquired_at,
            tax_year=item.acquired_at.year,
            entry_type=(
                JournalEntryType.REVIEW
                if "staking" in item.acquisition_type and item.gross_income_eur is None
                else (
                    JournalEntryType.EARN_INFLOW
                    if "staking" in item.acquisition_type
                    else JournalEntryType.ACQUISITION
                )
            ),
            asset_code=item.asset_code,
            quantity=item.quantity,
            eur_value=(
                item.gross_income_eur
                if item.gross_income_eur is not None
                else (
                    Decimal("0")
                    if "staking" in item.acquisition_type
                    else exact_decimal_sum((item.value_eur, item.fee_eur))
                )
            ),
            proceeds_eur=None,
            acquisition_cost_eur=exact_decimal_sum((item.value_eur, item.fee_eur)),
            gain_loss_eur=None,
            holding_seconds=None,
            classification=(
                "Prüfung: Bruttoertrag der historischen Rewardbewertung fehlt"
                if "staking" in item.acquisition_type and item.gross_income_eur is None
                else "Arbeitsdokumentation: privates Wirtschaftsgut"
            ),
            rule_version=rules.journal,
            status=(
                TaxRecordStatus.REVIEW_REQUIRED
                if "staking" in item.acquisition_type and item.gross_income_eur is None
                else TaxRecordStatus.RESOLVED
            ),
            source_object_type="AcquisitionLot",
            source_object_id=item.acquisition_id,
            valuation_decision_id=item.valuation_decision_id,
        )
        for item in ordered_acquisitions
        if period.start <= item.acquired_at.date() <= period.end
    ]
    journal.extend(
        TaxJournalEntry(
            tax_calculation_run_id=run_id,
            occurred_at=item.acquired_at,
            tax_year=item.acquired_at.year,
            entry_type=JournalEntryType.FEE,
            asset_code="EUR",
            quantity=item.platform_fee_candidate_eur,
            eur_value=item.platform_fee_candidate_eur,
            proceeds_eur=None,
            acquisition_cost_eur=None,
            gain_loss_eur=None,
            holding_seconds=None,
            classification=(
                "Manuell geprüft: Staking-Plattformgebühr als Werbungskosten "
                "berücksichtigt"
            ),
            rule_version=rules.journal,
            status=TaxRecordStatus.RESOLVED,
            source_object_type="ValuationDecision",
            source_object_id=item.valuation_decision_id,
            valuation_decision_id=item.valuation_decision_id,
            tax_review_decision_id=item.tax_review_decision_id,
        )
        for item in ordered_acquisitions
        if period.start <= item.acquired_at.date() <= period.end
        and item.platform_fee_decision
        is TaxReviewDecisionValue.INCLUDE_AS_WERBUNGSKOSTEN
        and item.platform_fee_candidate_eur > 0
    )
    allocations: list[LotAllocation] = []
    calculations: list[DisposalCalculation] = []
    reviews: list[TaxReviewCase] = []
    for disposal in sorted(
        (
            item
            for item in disposals
            if period.start <= item.disposed_at.date() <= period.end
            and item.asset_code not in NON_INVENTORY_ASSETS
        ),
        key=lambda item: (item.disposed_at, item.disposal_id.hex),
    ):
        remaining = disposal.quantity
        eligible = [
            lot
            for lot in lots
            if lot.asset_code == disposal.asset_code
            and lot.acquired_at <= disposal.disposed_at
            and lot.remaining_quantity > 0
        ]
        allocated = Decimal("0")
        costs = Decimal("0")
        proceeds = Decimal("0")
        fees = Decimal("0")
        for allocation_order, lot in enumerate(eligible, start=1):
            if remaining == 0:
                break
            quantity = min(remaining, lot.remaining_quantity)
            is_last_disposal_part = quantity == remaining
            is_last_lot_part = quantity == lot.remaining_quantity
            cost = (
                lot.remaining_cost_eur
                if is_last_lot_part
                else _share(
                    exact_decimal_sum(
                        (lot.acquisition_value_eur, lot.acquisition_fee_eur)
                    ),
                    quantity,
                    lot.original_quantity,
                )
            )
            part_proceeds = (
                exact_decimal_subtract(disposal.proceeds_eur, proceeds)
                if is_last_disposal_part
                else _share(disposal.proceeds_eur, quantity, disposal.quantity)
            )
            part_fee = (
                exact_decimal_subtract(disposal.fee_eur, fees)
                if is_last_disposal_part
                else _share(disposal.fee_eur, quantity, disposal.quantity)
            )
            gain = exact_decimal_sum(
                (part_proceeds, part_fee.copy_negate(), cost.copy_negate())
            )
            allocation = LotAllocation(
                tax_calculation_run_id=run_id,
                disposal_event_id=disposal.disposal_id,
                inventory_lot_id=lot.id,
                allocated_quantity=quantity,
                allocation_order=allocation_order,
                acquisition_cost_eur=cost,
                disposal_proceeds_eur=part_proceeds,
                disposal_fee_eur=part_fee,
                gain_loss_eur=gain,
                acquired_at=lot.acquired_at,
                disposed_at=disposal.disposed_at,
                holding_seconds=int(
                    (disposal.disposed_at - lot.acquired_at).total_seconds()
                ),
                fifo_rule_version=rules.fifo,
                fee_rule_version=rules.fees,
            )
            allocations.append(allocation)
            lot.remaining_quantity = exact_decimal_sum(
                (lot.remaining_quantity, quantity.copy_negate())
            )
            lot.remaining_cost_eur = exact_decimal_subtract(
                lot.remaining_cost_eur, cost
            )
            remaining = exact_decimal_subtract(remaining, quantity)
            allocated = exact_decimal_sum((allocated, quantity))
            costs = exact_decimal_sum((costs, cost))
            proceeds = exact_decimal_sum((proceeds, part_proceeds))
            fees = exact_decimal_sum((fees, part_fee))
            journal.append(
                TaxJournalEntry(
                    tax_calculation_run_id=run_id,
                    occurred_at=disposal.disposed_at,
                    tax_year=disposal.disposed_at.year,
                    entry_type=(
                        JournalEntryType.REALIZED_GAIN
                        if gain >= 0
                        else JournalEntryType.REALIZED_LOSS
                    ),
                    asset_code=disposal.asset_code,
                    quantity=quantity,
                    eur_value=abs(gain),
                    proceeds_eur=part_proceeds,
                    acquisition_cost_eur=cost,
                    gain_loss_eur=gain,
                    holding_seconds=allocation.holding_seconds,
                    classification="Arbeitsdokumentation: FIFO-Zuordnung",
                    rule_version=rules.journal,
                    status=TaxRecordStatus.RESOLVED,
                    source_object_type="DisposalEvent",
                    source_object_id=disposal.disposal_id,
                    valuation_decision_id=disposal.valuation_decision_id,
                    lot_allocation_id=allocation.id,
                )
            )
        status = TaxRecordStatus.RESOLVED
        if remaining > 0:
            status = TaxRecordStatus.REVIEW_REQUIRED
            review = TaxReviewCase(
                tax_calculation_run_id=run_id,
                code="tax_insufficient_inventory",
                message=(
                    "Für die Veräußerung ist kein vollständiger Bestand nachgewiesen."
                ),
                source_object_type="DisposalEvent",
                source_object_id=disposal.disposal_id,
                occurred_at=disposal.disposed_at,
            )
            reviews.append(review)
            journal.append(
                TaxJournalEntry(
                    tax_calculation_run_id=run_id,
                    occurred_at=disposal.disposed_at,
                    tax_year=disposal.disposed_at.year,
                    entry_type=JournalEntryType.REVIEW,
                    asset_code=disposal.asset_code,
                    quantity=remaining,
                    eur_value=Decimal("0"),
                    proceeds_eur=None,
                    acquisition_cost_eur=None,
                    gain_loss_eur=None,
                    holding_seconds=None,
                    classification=review.message,
                    rule_version=rules.journal,
                    status=status,
                    source_object_type="DisposalEvent",
                    source_object_id=disposal.disposal_id,
                    valuation_decision_id=disposal.valuation_decision_id,
                )
            )
        calculations.append(
            DisposalCalculation(
                tax_calculation_run_id=run_id,
                disposal_event_id=disposal.disposal_id,
                quantity=disposal.quantity,
                allocated_quantity=allocated,
                proceeds_eur=proceeds,
                acquisition_cost_eur=costs,
                fees_eur=fees,
                gain_loss_eur=exact_decimal_sum(
                    (proceeds, fees.copy_negate(), costs.copy_negate())
                ),
                status=status,
                rule_version=rules.classification,
            )
        )
        journal.append(
            TaxJournalEntry(
                tax_calculation_run_id=run_id,
                occurred_at=disposal.disposed_at,
                tax_year=disposal.disposed_at.year,
                entry_type=(
                    JournalEntryType.EXCHANGE
                    if "exchange" in disposal.disposal_type
                    else JournalEntryType.DISPOSAL
                ),
                asset_code=disposal.asset_code,
                quantity=disposal.quantity,
                eur_value=disposal.proceeds_eur,
                proceeds_eur=disposal.proceeds_eur,
                acquisition_cost_eur=costs,
                gain_loss_eur=exact_decimal_sum(
                    (proceeds, fees.copy_negate(), costs.copy_negate())
                ),
                holding_seconds=None,
                classification="Arbeitsdokumentation: Veräußerung",
                rule_version=rules.journal,
                status=status,
                source_object_type="DisposalEvent",
                source_object_id=disposal.disposal_id,
                valuation_decision_id=disposal.valuation_decision_id,
            )
        )
        if disposal.fee_eur > 0:
            journal.append(
                TaxJournalEntry(
                    tax_calculation_run_id=run_id,
                    occurred_at=disposal.disposed_at,
                    tax_year=disposal.disposed_at.year,
                    entry_type=JournalEntryType.FEE,
                    asset_code="EUR",
                    quantity=disposal.fee_eur,
                    eur_value=disposal.fee_eur,
                    proceeds_eur=None,
                    acquisition_cost_eur=None,
                    gain_loss_eur=None,
                    holding_seconds=None,
                    classification="Arbeitsdokumentation: Veräußerungsgebühr",
                    rule_version=rules.journal,
                    status=status,
                    source_object_type="DisposalEvent",
                    source_object_id=disposal.disposal_id,
                    valuation_decision_id=disposal.valuation_decision_id,
                )
            )
    journal.sort(key=lambda item: (item.occurred_at, item.id.hex))
    return FifoResult(
        lots=tuple(lots),
        allocations=tuple(allocations),
        calculations=tuple(calculations),
        journal=tuple(journal),
        reviews=tuple(reviews),
    )
