from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import Mapper
from sqlalchemy.types import Uuid

from app.core.entities import (
    AuditActorType,
    AuditEvent,
    Configuration,
    EarnLot,
    ErrorCategory,
    ImportError,
    ImportSession,
    ImportStatus,
    PriceSnapshot,
    RawImportRecord,
    Sale,
)
from app.core.tax import (
    DisposalCalculation,
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
    TaxReviewCase,
    TaxReviewDecision,
    TaxReviewDecisionValue,
    TaxRunStatus,
)
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    DecisionType,
    DisposalEvent,
    DisposalType,
    DomainProvenance,
    FeeEvent,
    ReconciliationStatus,
    TaxTreatmentHint,
    TradeExecution,
    TransformationDecision,
    TransformationIssue,
    TransformationRun,
    TransformationRunSession,
    TransformationStatus,
    ValuationMethod,
    ValuationRequirement,
    ValuationStatus,
)
from app.core.valuation import (
    DailyPrice,
    FeeTaxClassification,
    FeeTaxReviewStatus,
    PriceMethod,
    ProviderEvidence,
    ValuationDecision,
    ValuationDecisionStatus,
    ValuationRun,
    ValuationRunStatus,
)
from app.database.base import mapper_registry
from app.database.types import STRUCTURED_JSON, ExactDecimal, UtcDateTime

UUID = Uuid(as_uuid=True)
AMOUNT = ExactDecimal()
COIN = String(32)
SOURCE = String(128)

import_sessions = Table(
    "import_sessions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("source", SOURCE, nullable=False),
    Column("version", String(64), nullable=False),
    Column("status", Enum(ImportStatus, native_enum=False), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("correlation_id", UUID, nullable=False, unique=True),
    Column("actor_type", Enum(AuditActorType, native_enum=False), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("ended_at", UtcDateTime(), nullable=True),
    Column("received_count", Integer, nullable=False),
    Column("persisted_count", Integer, nullable=False),
    Column("skipped_count", Integer, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    Column("import_hash", String(64), nullable=True),
    Column("error_summary", String(1024), nullable=True),
    CheckConstraint(
        "import_hash IS NULL OR length(import_hash) = 64",
        name="ck_import_sessions_hash_length",
    ),
)


def import_reference() -> Column[Any]:
    return Column(
        "import_session_id",
        UUID,
        ForeignKey("import_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    )


earn_lots = Table(
    "earn_lots",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("lot_id", UUID, nullable=False, unique=True),
    Column("coin", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
    import_reference(),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    CheckConstraint("quantity > 0", name="ck_earn_lots_quantity_positive"),
)

sales = Table(
    "sales",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("coin", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
    import_reference(),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
    CheckConstraint("quantity > 0", name="ck_sales_quantity_positive"),
)

audit_events = Table(
    "audit_events",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("entity_type", String(128), nullable=False),
    Column("entity_id", UUID, nullable=False),
    Column("actor_type", Enum(AuditActorType, native_enum=False), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("metadata", STRUCTURED_JSON, nullable=False),
)

price_snapshots = Table(
    "price_snapshots",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("coin", COIN, nullable=False),
    Column("captured_at", UtcDateTime(), nullable=False),
    Column("price_eur", AMOUNT, nullable=False),
    Column("source", SOURCE, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    CheckConstraint("price_eur > 0", name="ck_price_snapshots_price_positive"),
)

configurations = Table(
    "configurations",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("updated_at", UtcDateTime(), nullable=False),
)

raw_import_records = Table(
    "raw_import_records",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    import_reference(),
    Column("source", SOURCE, nullable=False),
    Column("content_hash", String(128), nullable=False),
    Column("payload", STRUCTURED_JSON, nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    Column("sequence_number", Integer, nullable=False),
    Column("external_id", String(255), nullable=True),
    Column("canonical_key", String(512), nullable=True),
    Column("technical_metadata", STRUCTURED_JSON, nullable=False),
    UniqueConstraint("canonical_key", name="uq_raw_import_canonical_key"),
    UniqueConstraint(
        "import_session_id",
        "sequence_number",
        name="uq_raw_import_session_sequence",
    ),
)

import_errors = Table(
    "import_errors",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    import_reference(),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("category", Enum(ErrorCategory, native_enum=False), nullable=False),
    Column("error_code", String(128), nullable=False),
    Column("description", String(1024), nullable=False),
    Column("original_exception", String(2048), nullable=False),
    Column("affected_record", STRUCTURED_JSON, nullable=True),
)

transformation_runs = Table(
    "transformation_runs",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("contract_version", String(64), nullable=False),
    Column("status", Enum(TransformationStatus, native_enum=False), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("completed_at", UtcDateTime(), nullable=True),
    Column("actor_id", String(255), nullable=False),
    Column("checked_records", Integer, nullable=False),
    Column("created_objects", Integer, nullable=False),
    Column("internal_movements", Integer, nullable=False),
    Column("review_cases", Integer, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("error_summary", String(1024), nullable=True),
    Column("created_at", UtcDateTime(), nullable=False),
    CheckConstraint(
        "checked_records >= 0 AND created_objects >= 0 "
        "AND internal_movements >= 0 AND review_cases >= 0 "
        "AND error_count >= 0",
        name="ck_transformation_run_counts_non_negative",
    ),
)

transformation_run_sessions = Table(
    "transformation_run_sessions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "transformation_run_id",
        UUID,
        ForeignKey("transformation_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "import_session_id",
        UUID,
        ForeignKey("import_sessions.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    UniqueConstraint(
        "transformation_run_id",
        "import_session_id",
        name="uq_transformation_run_session",
    ),
)

transformation_decisions = Table(
    "transformation_decisions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "raw_import_record_id",
        UUID,
        ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    import_reference(),
    Column(
        "transformation_run_id",
        UUID,
        ForeignKey("transformation_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("contract_version", String(64), nullable=False),
    Column("decision_type", Enum(DecisionType, native_enum=False), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("explanation", String(1024), nullable=False),
    Column("domain_object_id", UUID, nullable=True),
    Column("decided_at", UtcDateTime(), nullable=False),
    UniqueConstraint(
        "transformation_run_id",
        "raw_import_record_id",
        name="uq_transformation_decision_run_raw",
    ),
)

transformation_issues = Table(
    "transformation_issues",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "transformation_run_id",
        UUID,
        ForeignKey("transformation_runs.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "raw_import_record_id",
        UUID,
        ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("code", String(128), nullable=False),
    Column("message", String(1024), nullable=False),
    Column("is_conflict", Boolean, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
)


def projection_columns() -> list[Column[Any]]:
    return [
        Column("id", UUID, primary_key=True),
        Column("stable_key", String(512), nullable=False, unique=True),
        Column("payload_hash", String(64), nullable=False),
        Column("external_id", String(255), nullable=False),
        Column("transformation_version", String(64), nullable=False),
        Column("provider", String(64), nullable=False),
        Column("occurred_at", UtcDateTime(), nullable=False),
        Column("created_at", UtcDateTime(), nullable=False),
    ]


acquisition_lots = Table(
    "acquisition_lots",
    mapper_registry.metadata,
    *projection_columns(),
    Column("asset_raw_code", COIN, nullable=False),
    Column("asset_code", COIN, nullable=False),
    Column("asset_mapping_version", String(64), nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column(
        "acquisition_type",
        Enum(AcquisitionType, native_enum=False),
        nullable=False,
    ),
    Column("account_scope", String(64), nullable=False),
    Column("wallet_scope", String(64), nullable=False),
    Column(
        "valuation_status", Enum(ValuationStatus, native_enum=False), nullable=False
    ),
    Column(
        "tax_treatment_hint", Enum(TaxTreatmentHint, native_enum=False), nullable=False
    ),
    Column("native_consideration_asset", COIN, nullable=True),
    Column("native_consideration_quantity", AMOUNT, nullable=True),
    Column("fee_quantity", AMOUNT, nullable=False),
    Column("fee_asset", COIN, nullable=True),
    Column("gross_quantity", AMOUNT, nullable=True),
    CheckConstraint("quantity > 0", name="ck_acquisition_quantity_positive"),
    CheckConstraint("fee_quantity >= 0", name="ck_acquisition_fee_non_negative"),
)

trade_executions = Table(
    "trade_executions",
    mapper_registry.metadata,
    *projection_columns(),
    Column("order_external_id", String(255), nullable=False),
    Column("raw_pair", String(64), nullable=False),
    Column("base_asset_raw", COIN, nullable=False),
    Column("base_asset", COIN, nullable=False),
    Column("quote_asset_raw", COIN, nullable=False),
    Column("quote_asset", COIN, nullable=False),
    Column("side", String(16), nullable=False),
    Column("order_type", String(32), nullable=False),
    Column("volume", AMOUNT, nullable=False),
    Column("price", AMOUNT, nullable=False),
    Column("cost", AMOUNT, nullable=False),
    Column("fee", AMOUNT, nullable=False),
    Column("fee_asset", COIN, nullable=True),
    Column(
        "reconciliation_status",
        Enum(ReconciliationStatus, native_enum=False),
        nullable=False,
    ),
    CheckConstraint("volume > 0", name="ck_trade_volume_positive"),
    CheckConstraint("price > 0", name="ck_trade_price_positive"),
    CheckConstraint("cost > 0", name="ck_trade_cost_positive"),
    CheckConstraint("fee >= 0", name="ck_trade_fee_non_negative"),
)

disposal_events = Table(
    "disposal_events",
    mapper_registry.metadata,
    *projection_columns(),
    Column("asset_raw_code", COIN, nullable=False),
    Column("asset_code", COIN, nullable=False),
    Column("asset_mapping_version", String(64), nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("disposal_type", Enum(DisposalType, native_enum=False), nullable=False),
    Column("account_scope", String(64), nullable=False),
    Column("wallet_scope", String(64), nullable=False),
    Column(
        "valuation_status", Enum(ValuationStatus, native_enum=False), nullable=False
    ),
    Column(
        "tax_treatment_hint", Enum(TaxTreatmentHint, native_enum=False), nullable=False
    ),
    Column("native_consideration_asset", COIN, nullable=True),
    Column("native_consideration_quantity", AMOUNT, nullable=True),
    Column("fee_quantity", AMOUNT, nullable=False),
    Column("fee_asset", COIN, nullable=True),
    Column("fifo_status", String(32), nullable=False),
    Column(
        "trade_execution_id",
        UUID,
        ForeignKey("trade_executions.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    CheckConstraint("quantity > 0", name="ck_disposal_quantity_positive"),
    CheckConstraint("fee_quantity >= 0", name="ck_disposal_fee_non_negative"),
)

fee_events = Table(
    "fee_events",
    mapper_registry.metadata,
    *projection_columns(),
    Column("asset_code", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column(
        "valuation_status", Enum(ValuationStatus, native_enum=False), nullable=False
    ),
    Column("related_object_id", UUID, nullable=False),
    CheckConstraint("quantity > 0", name="ck_fee_quantity_positive"),
)

domain_provenance = Table(
    "domain_provenance",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("domain_object_type", String(64), nullable=False),
    Column("domain_object_id", UUID, nullable=False),
    Column(
        "raw_import_record_id",
        UUID,
        ForeignKey("raw_import_records.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    import_reference(),
    Column(
        "transformation_run_id",
        UUID,
        ForeignKey("transformation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    UniqueConstraint(
        "domain_object_type",
        "domain_object_id",
        "raw_import_record_id",
        name="uq_domain_provenance_object_raw",
    ),
)

valuation_requirements = Table(
    "valuation_requirements",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("asset_code", COIN, nullable=False),
    Column("target_currency", COIN, nullable=False),
    Column("valuation_at", UtcDateTime(), nullable=False),
    Column("method", Enum(ValuationMethod, native_enum=False), nullable=False),
    Column("status", Enum(ValuationStatus, native_enum=False), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("domain_object_type", String(64), nullable=False),
    Column("domain_object_id", UUID, nullable=False),
    Column(
        "transformation_run_id",
        UUID,
        ForeignKey("transformation_runs.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("created_at", UtcDateTime(), nullable=False),
    UniqueConstraint(
        "domain_object_type",
        "domain_object_id",
        name="uq_valuation_requirement_object",
    ),
)

valuation_runs = Table(
    "valuation_runs",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("contract_version", String(64), nullable=False),
    Column("method_version", String(64), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("correlation_id", UUID, nullable=False, unique=True),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("ended_at", UtcDateTime(), nullable=True),
    Column("status", Enum(ValuationRunStatus, native_enum=False), nullable=False),
    Column("checked_requirements", Integer, nullable=False),
    Column("resolved_requirements", Integer, nullable=False),
    Column("native_count", Integer, nullable=False),
    Column("automatic_count", Integer, nullable=False),
    Column("manual_count", Integer, nullable=False),
    Column("review_count", Integer, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("error_summary", String(1024), nullable=True),
    CheckConstraint(
        "checked_requirements >= 0 AND resolved_requirements >= 0 "
        "AND native_count >= 0 AND automatic_count >= 0 "
        "AND manual_count >= 0 AND review_count >= 0 AND error_count >= 0",
        name="ck_valuation_run_counts",
    ),
)

daily_prices = Table(
    "daily_prices",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("asset_code", COIN, nullable=False),
    Column("price_date", Date, nullable=False),
    Column("unit_price_eur", AMOUNT, nullable=False),
    Column("method", Enum(PriceMethod, native_enum=False), nullable=False),
    Column("source", String(255), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("provider_contract_version", String(64), nullable=False),
    Column("evidence_hash", String(64), nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("earliest_sample_at", UtcDateTime(), nullable=True),
    Column("latest_sample_at", UtcDateTime(), nullable=True),
    Column("minimum_price_eur", AMOUNT, nullable=True),
    Column("maximum_price_eur", AMOUNT, nullable=True),
    Column("fetched_at", UtcDateTime(), nullable=False),
    Column("status", Enum(ValuationDecisionStatus, native_enum=False), nullable=False),
    Column("version", Integer, nullable=False),
    Column("external_reference", String(512), nullable=True),
    Column("reason", String(1024), nullable=True),
    Column("entered_by", String(255), nullable=True),
    Column("supersedes_id", UUID, ForeignKey("daily_prices.id"), nullable=True),
    Column(
        "provider_evidence_id",
        UUID,
        ForeignKey("provider_evidence.id"),
        nullable=True,
    ),
    UniqueConstraint(
        "asset_code",
        "price_date",
        "method",
        "provider",
        "provider_contract_version",
        "evidence_hash",
        name="uq_daily_price_evidence",
    ),
    UniqueConstraint(
        "asset_code",
        "price_date",
        "method",
        "provider",
        "provider_contract_version",
        "version",
        name="uq_daily_price_version",
    ),
    CheckConstraint(
        "unit_price_eur > 0 AND sample_count >= 0 AND version > 0 "
        "AND length(evidence_hash) = 64",
        name="ck_daily_price_values",
    ),
)

valuation_decisions = Table(
    "valuation_decisions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "valuation_requirement_id",
        UUID,
        ForeignKey("valuation_requirements.id"),
        nullable=False,
    ),
    Column("valuation_run_id", UUID, ForeignKey("valuation_runs.id"), nullable=False),
    Column("domain_object_type", String(64), nullable=False),
    Column("domain_object_id", UUID, nullable=False),
    Column("asset_code", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("valuation_at", UtcDateTime(), nullable=False),
    Column("price_date", Date, nullable=False),
    Column("method", Enum(PriceMethod, native_enum=False), nullable=False),
    Column("unit_price_eur", AMOUNT, nullable=False),
    Column("eur_value", AMOUNT, nullable=False),
    Column("price_source", String(255), nullable=False),
    Column("provider", String(64), nullable=False),
    Column("provider_object_id", UUID, ForeignKey("daily_prices.id"), nullable=True),
    Column("provider_contract_version", String(64), nullable=False),
    Column("method_version", String(64), nullable=False),
    Column("sample_count", Integer, nullable=False),
    Column("fetched_at", UtcDateTime(), nullable=False),
    Column("decided_at", UtcDateTime(), nullable=False),
    Column("status", Enum(ValuationDecisionStatus, native_enum=False), nullable=False),
    Column("reason_code", String(128), nullable=False),
    Column("version", Integer, nullable=False),
    Column("rounding_rule", String(64), nullable=False),
    Column("gross_quantity", AMOUNT, nullable=True),
    Column("fee_quantity", AMOUNT, nullable=True),
    Column("net_quantity", AMOUNT, nullable=True),
    Column("gross_income_eur", AMOUNT, nullable=True),
    Column("fee_value_eur", AMOUNT, nullable=True),
    Column("net_acquisition_value_eur", AMOUNT, nullable=True),
    Column("valuation_basis", String(128), nullable=True),
    Column(
        "fee_tax_classification",
        Enum(FeeTaxClassification, native_enum=False),
        nullable=True,
    ),
    Column(
        "fee_tax_review_status",
        Enum(FeeTaxReviewStatus, native_enum=False),
        nullable=True,
    ),
    Column("supersedes_id", UUID, ForeignKey("valuation_decisions.id"), nullable=True),
    Column(
        "provider_evidence_id",
        UUID,
        ForeignKey("provider_evidence.id"),
        nullable=True,
    ),
    UniqueConstraint(
        "valuation_requirement_id", "version", name="uq_valuation_decision_version"
    ),
    CheckConstraint(
        "quantity > 0 AND unit_price_eur > 0 AND eur_value > 0 "
        "AND sample_count >= 0 AND version > 0",
        name="ck_valuation_decision_values",
    ),
    CheckConstraint(
        "(gross_quantity IS NULL OR gross_quantity > 0) AND "
        "(fee_quantity IS NULL OR fee_quantity >= 0) AND "
        "(net_quantity IS NULL OR net_quantity > 0) AND "
        "(gross_income_eur IS NULL OR gross_income_eur > 0) AND "
        "(fee_value_eur IS NULL OR fee_value_eur >= 0) AND "
        "(net_acquisition_value_eur IS NULL OR "
        "net_acquisition_value_eur > 0)",
        name="ck_valuation_reward_components",
    ),
)

provider_evidence = Table(
    "provider_evidence",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("provider", String(64), nullable=False),
    Column("provider_contract_version", String(64), nullable=False),
    Column("provider_asset_id", String(128), nullable=False),
    Column("target_currency", COIN, nullable=False),
    Column("requested_from", UtcDateTime(), nullable=False),
    Column("requested_to", UtcDateTime(), nullable=False),
    Column("fetched_at", UtcDateTime(), nullable=False),
    Column("http_status", Integer, nullable=False),
    Column("response_hash", String(64), nullable=False),
    Column("observation_count", Integer, nullable=False),
    Column("earliest_observed_at", UtcDateTime(), nullable=True),
    Column("latest_observed_at", UtcDateTime(), nullable=True),
    Column("observations", STRUCTURED_JSON, nullable=False),
    CheckConstraint(
        "http_status >= 100 AND http_status <= 599 AND observation_count >= 0 "
        "AND requested_to > requested_from AND target_currency = 'EUR' "
        "AND length(response_hash) = 64",
        name="ck_provider_evidence_values",
    ),
    UniqueConstraint(
        "provider",
        "provider_contract_version",
        "provider_asset_id",
        "target_currency",
        "requested_from",
        "requested_to",
        "response_hash",
        name="uq_provider_evidence_identity",
    ),
)

tax_calculation_runs = Table(
    "tax_calculation_runs",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("snapshot_hash", String(64), nullable=False),
    Column("rules_fingerprint", String(64), nullable=False),
    Column("status", Enum(TaxRunStatus, native_enum=False), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("fifo_rule_version", String(64), nullable=False),
    Column("fee_rule_version", String(64), nullable=False),
    Column("classification_rule_version", String(64), nullable=False),
    Column("journal_rule_version", String(64), nullable=False),
    Column("export_format_version", String(64), nullable=False),
    Column("ended_at", UtcDateTime(), nullable=True),
    Column("checked_events", Integer, nullable=False),
    Column("created_allocations", Integer, nullable=False),
    Column("created_journal_entries", Integer, nullable=False),
    Column("review_count", Integer, nullable=False),
    Column("error_count", Integer, nullable=False),
    Column("error_summary", String(1024), nullable=True),
    Column("supersedes_id", UUID, ForeignKey("tax_calculation_runs.id")),
    UniqueConstraint(
        "period_start",
        "period_end",
        "snapshot_hash",
        "rules_fingerprint",
        name="uq_tax_run_identity",
    ),
    CheckConstraint(
        "period_end >= period_start AND checked_events >= 0 "
        "AND created_allocations >= 0 AND created_journal_entries >= 0 "
        "AND review_count >= 0 AND error_count >= 0",
        name="ck_tax_run_values",
    ),
)

inventory_lots = Table(
    "inventory_lots",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column(
        "acquisition_lot_id", UUID, ForeignKey("acquisition_lots.id"), nullable=False
    ),
    Column("asset_code", COIN, nullable=False),
    Column("original_quantity", AMOUNT, nullable=False),
    Column("remaining_quantity", AMOUNT, nullable=False),
    Column("acquired_at", UtcDateTime(), nullable=False),
    Column("acquisition_value_eur", AMOUNT, nullable=False),
    Column("acquisition_fee_eur", AMOUNT, nullable=False),
    Column("remaining_cost_eur", AMOUNT, nullable=False),
    Column(
        "valuation_decision_id",
        UUID,
        ForeignKey("valuation_decisions.id"),
        nullable=False,
    ),
    Column("rule_version", String(64), nullable=False),
    Column("sequence", Integer, nullable=False),
    UniqueConstraint(
        "tax_calculation_run_id",
        "acquisition_lot_id",
        name="uq_inventory_run_acquisition",
    ),
    Index("ix_inventory_asset_remaining", "asset_code", "remaining_quantity"),
    CheckConstraint(
        "original_quantity > 0 AND remaining_quantity >= 0 "
        "AND remaining_quantity <= original_quantity AND acquisition_value_eur > 0 "
        "AND acquisition_fee_eur >= 0 AND remaining_cost_eur >= 0 AND sequence >= 0",
        name="ck_inventory_lot_values",
    ),
)

lot_allocations = Table(
    "lot_allocations",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column("disposal_event_id", UUID, ForeignKey("disposal_events.id"), nullable=False),
    Column("inventory_lot_id", UUID, ForeignKey("inventory_lots.id"), nullable=False),
    Column("allocated_quantity", AMOUNT, nullable=False),
    Column("allocation_order", Integer, nullable=False),
    Column("acquisition_cost_eur", AMOUNT, nullable=False),
    Column("disposal_proceeds_eur", AMOUNT, nullable=False),
    Column("disposal_fee_eur", AMOUNT, nullable=False),
    Column("gain_loss_eur", AMOUNT, nullable=False),
    Column("acquired_at", UtcDateTime(), nullable=False),
    Column("disposed_at", UtcDateTime(), nullable=False),
    Column("holding_seconds", Integer, nullable=False),
    Column("fifo_rule_version", String(64), nullable=False),
    Column("fee_rule_version", String(64), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    UniqueConstraint(
        "tax_calculation_run_id",
        "disposal_event_id",
        "allocation_order",
        name="uq_allocation_order",
    ),
    Index("ix_allocations_disposal", "disposal_event_id", "allocation_order"),
    CheckConstraint(
        "allocated_quantity > 0 AND allocation_order > 0 "
        "AND acquisition_cost_eur >= 0 AND disposal_proceeds_eur >= 0 "
        "AND disposal_fee_eur >= 0 AND holding_seconds >= 0",
        name="ck_lot_allocation_values",
    ),
)

disposal_calculations = Table(
    "disposal_calculations",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column("disposal_event_id", UUID, ForeignKey("disposal_events.id"), nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("allocated_quantity", AMOUNT, nullable=False),
    Column("proceeds_eur", AMOUNT, nullable=False),
    Column("acquisition_cost_eur", AMOUNT, nullable=False),
    Column("fees_eur", AMOUNT, nullable=False),
    Column("gain_loss_eur", AMOUNT, nullable=False),
    Column("status", Enum(TaxRecordStatus, native_enum=False), nullable=False),
    Column("rule_version", String(64), nullable=False),
    UniqueConstraint(
        "tax_calculation_run_id",
        "disposal_event_id",
        name="uq_disposal_calculation_run",
    ),
)

tax_journal_entries = Table(
    "tax_journal_entries",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column("occurred_at", UtcDateTime(), nullable=False),
    Column("tax_year", Integer, nullable=False),
    Column("entry_type", Enum(JournalEntryType, native_enum=False), nullable=False),
    Column("asset_code", COIN, nullable=False),
    Column("quantity", AMOUNT, nullable=False),
    Column("eur_value", AMOUNT, nullable=False),
    Column("proceeds_eur", AMOUNT),
    Column("acquisition_cost_eur", AMOUNT),
    Column("gain_loss_eur", AMOUNT),
    Column("holding_seconds", Integer),
    Column("classification", String(255), nullable=False),
    Column("rule_version", String(64), nullable=False),
    Column("status", Enum(TaxRecordStatus, native_enum=False), nullable=False),
    Column("source_object_type", String(64), nullable=False),
    Column("source_object_id", UUID, nullable=False),
    Column("valuation_decision_id", UUID, ForeignKey("valuation_decisions.id")),
    Column("lot_allocation_id", UUID, ForeignKey("lot_allocations.id")),
    Column("supersedes_id", UUID, ForeignKey("tax_journal_entries.id")),
    Column(
        "tax_review_decision_id",
        UUID,
        ForeignKey("tax_review_decisions.id", name="fk_tax_journal_review_decision"),
    ),
    UniqueConstraint(
        "tax_calculation_run_id",
        "entry_type",
        "source_object_id",
        "lot_allocation_id",
        name="uq_tax_journal_source",
    ),
    Index("ix_tax_journal_year_type", "tax_year", "entry_type"),
)

tax_review_cases = Table(
    "tax_review_cases",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column("code", String(128), nullable=False),
    Column("message", String(1024), nullable=False),
    Column("source_object_type", String(64), nullable=False),
    Column("source_object_id", UUID, nullable=False),
    Column("occurred_at", UtcDateTime(), nullable=False),
    UniqueConstraint(
        "tax_calculation_run_id",
        "code",
        "source_object_id",
        name="uq_tax_review_source",
    ),
)

tax_review_decisions = Table(
    "tax_review_decisions",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "valuation_decision_id",
        UUID,
        ForeignKey("valuation_decisions.id"),
        nullable=False,
    ),
    Column(
        "source_tax_review_case_id",
        UUID,
        ForeignKey("tax_review_cases.id"),
        nullable=False,
    ),
    Column("decision", Enum(TaxReviewDecisionValue, native_enum=False), nullable=False),
    Column("reason", String(1024), nullable=False),
    Column("actor_id", String(255), nullable=False),
    Column("decided_at", UtcDateTime(), nullable=False),
    Column("version", Integer, nullable=False),
    Column("supersedes_id", UUID, ForeignKey("tax_review_decisions.id")),
    Column("batch_id", UUID, nullable=False),
    UniqueConstraint(
        "valuation_decision_id", "version", name="uq_tax_review_decision_version"
    ),
    CheckConstraint(
        "version > 0 AND length(reason) > 0 AND length(actor_id) > 0",
        name="ck_tax_review_decision_values",
    ),
    Index("ix_tax_review_decision_batch", "batch_id"),
)

export_runs = Table(
    "export_runs",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "tax_calculation_run_id",
        UUID,
        ForeignKey("tax_calculation_runs.id"),
        nullable=False,
    ),
    Column("kind", Enum(ExportKind, native_enum=False), nullable=False),
    Column("status", Enum(ExportStatus, native_enum=False), nullable=False),
    Column("period_start", Date, nullable=False),
    Column("period_end", Date, nullable=False),
    Column("rules_fingerprint", String(64), nullable=False),
    Column("format_version", String(64), nullable=False),
    Column("started_at", UtcDateTime(), nullable=False),
    Column("completed_at", UtcDateTime()),
    Column("error_summary", String(1024)),
    UniqueConstraint(
        "tax_calculation_run_id",
        "kind",
        "format_version",
        name="uq_export_run_format",
    ),
)

export_artifacts = Table(
    "export_artifacts",
    mapper_registry.metadata,
    Column("id", UUID, primary_key=True),
    Column(
        "export_run_id", UUID, ForeignKey("export_runs.id"), nullable=False, unique=True
    ),
    Column("kind", Enum(ExportKind, native_enum=False), nullable=False),
    Column("file_name", String(255), nullable=False, unique=True),
    Column("media_type", String(128), nullable=False),
    Column("size_bytes", Integer, nullable=False),
    Column("sha256_hash", String(64), nullable=False),
    Column("created_at", UtcDateTime(), nullable=False),
    CheckConstraint(
        "size_bytes >= 0 AND length(sha256_hash) = 64", name="ck_export_artifact_values"
    ),
)


def reject_update(_: Mapper[Any], connection: Any, target: object) -> None:
    del connection, target
    raise ValueError("Immutable records cannot be updated.")


def configure_mappings() -> None:
    """Register entities with persistence mappings exactly once."""
    if list(mapper_registry.mappers):
        return
    immutable_mappers = [
        mapper_registry.map_imperatively(EarnLot, earn_lots),
        mapper_registry.map_imperatively(AuditEvent, audit_events),
        mapper_registry.map_imperatively(PriceSnapshot, price_snapshots),
        mapper_registry.map_imperatively(RawImportRecord, raw_import_records),
        mapper_registry.map_imperatively(ImportError, import_errors),
        mapper_registry.map_imperatively(
            TransformationRunSession, transformation_run_sessions
        ),
        mapper_registry.map_imperatively(
            TransformationDecision, transformation_decisions
        ),
        mapper_registry.map_imperatively(TransformationIssue, transformation_issues),
        mapper_registry.map_imperatively(AcquisitionLot, acquisition_lots),
        mapper_registry.map_imperatively(DisposalEvent, disposal_events),
        mapper_registry.map_imperatively(TradeExecution, trade_executions),
        mapper_registry.map_imperatively(FeeEvent, fee_events),
        mapper_registry.map_imperatively(DomainProvenance, domain_provenance),
        mapper_registry.map_imperatively(ValuationRequirement, valuation_requirements),
        mapper_registry.map_imperatively(DailyPrice, daily_prices),
        mapper_registry.map_imperatively(ValuationDecision, valuation_decisions),
        mapper_registry.map_imperatively(ProviderEvidence, provider_evidence),
        mapper_registry.map_imperatively(InventoryLot, inventory_lots),
        mapper_registry.map_imperatively(LotAllocation, lot_allocations),
        mapper_registry.map_imperatively(DisposalCalculation, disposal_calculations),
        mapper_registry.map_imperatively(TaxJournalEntry, tax_journal_entries),
        mapper_registry.map_imperatively(TaxReviewCase, tax_review_cases),
        mapper_registry.map_imperatively(TaxReviewDecision, tax_review_decisions),
        mapper_registry.map_imperatively(ExportArtifact, export_artifacts),
    ]
    mapper_registry.map_imperatively(ImportSession, import_sessions)
    mapper_registry.map_imperatively(TransformationRun, transformation_runs)
    mapper_registry.map_imperatively(ValuationRun, valuation_runs)
    mapper_registry.map_imperatively(TaxCalculationRun, tax_calculation_runs)
    mapper_registry.map_imperatively(ExportRun, export_runs)
    mapper_registry.map_imperatively(Sale, sales)
    mapper_registry.map_imperatively(Configuration, configurations)
    for mapper in immutable_mappers:
        event.listen(mapper, "before_update", reject_update)
