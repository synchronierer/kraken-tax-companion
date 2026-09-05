from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from app.adapters.kraken.assets import (
    ASSET_MAPPING_VERSION,
    FIAT_ASSETS,
    resolve_asset,
    resolve_asset_legacy_v1,
    resolve_pair,
)
from app.core.entities import AuditActorType, AuditEvent, RawImportRecord
from app.core.time import utc_now
from app.core.transformation import (
    AcquisitionLot,
    AcquisitionType,
    AssetIdentity,
    DecisionType,
    DisposalEvent,
    DisposalType,
    DomainProvenance,
    FeeEvent,
    MappingStatus,
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
from app.core.unit_of_work import UnitOfWork
from app.transformations.state_machine import transition_transformation

INTERNAL_SUBTYPES = frozenset(
    {
        "allocation",
        "autoallocate",
        "deallocation",
        "migration",
        "spottostaking",
        "stakingfromspot",
        "stakingtospot",
        "spotfromstaking",
    }
)
FINANCIAL_LEDGER_TYPES_REQUIRING_REVIEW = frozenset(
    {
        "adjustment",
        "credit",
        "deposit",
        "dividend",
        "margin",
        "nft_rebate",
        "rollover",
        "sale",
        "settled",
        "trade",
        "transfer",
        "withdrawal",
    }
)
TRANSFORMATION_CONTRACT_VERSION = "kraken-domain-v2"
DOMAIN_IDENTITY_VERSION = "kraken-domain-v1"
PROVIDER = "kraken"
ACCOUNT_SCOPE = "default"
WALLET_SCOPE = "kraken-spot"
COST_TOLERANCE = Decimal("0.01")


class TransformationProblemKind(StrEnum):
    REVIEW = "review"
    CONFLICT = "conflict"
    INFRASTRUCTURE = "infrastructure"


@dataclass(frozen=True, kw_only=True)
class TransformationProblem:
    code: str
    message: str
    kind: TransformationProblemKind
    raw_import_record_id: UUID | None = None


@dataclass(frozen=True, kw_only=True)
class TransformationResult:
    run_id: UUID
    status: TransformationStatus
    checked_records: int
    rewards: int
    acquisitions: int
    disposals: int
    trade_executions: int
    fee_events: int
    internal_movements: int
    review_cases: int
    conflicts: int
    valuation_requirements: int
    reused_objects: int
    problems: tuple[TransformationProblem, ...]


@dataclass
class _Counters:
    rewards: int = 0
    acquisitions: int = 0
    disposals: int = 0
    trades: int = 0
    fees: int = 0
    internal: int = 0
    reviews: int = 0
    conflicts: int = 0
    valuations: int = 0
    reused: int = 0

    @property
    def created(self) -> int:
        return (
            self.acquisitions
            + self.disposals
            + self.trades
            + self.fees
            + self.valuations
        )


class KrakenTransformationService:
    def __init__(
        self,
        *,
        unit_of_work_factory: Callable[[], UnitOfWork],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._clock = clock

    def transform(
        self,
        *,
        import_session_ids: Sequence[UUID],
        context_import_session_ids: Sequence[UUID] = (),
        actor_id: str,
        contract_version: str = TRANSFORMATION_CONTRACT_VERSION,
    ) -> TransformationResult:
        started_at = self._clock()
        run = TransformationRun(
            contract_version=contract_version,
            status=TransformationStatus.CREATED,
            started_at=started_at,
            actor_id=actor_id,
        )
        counters = _Counters()
        problems: list[TransformationProblem] = []
        try:
            with self._unit_of_work_factory() as unit:
                unit.transformation_runs.add(run)
                linked_sessions = tuple(
                    dict.fromkeys((*import_session_ids, *context_import_session_ids))
                )
                for session_id in linked_sessions:
                    unit.transformation_run_sessions.add(
                        TransformationRunSession(
                            transformation_run_id=run.id,
                            import_session_id=session_id,
                        )
                    )
                self._audit(unit, run, "transformation.created", {})
                transition_transformation(
                    run, TransformationStatus.PROCESSING, self._clock()
                )
                self._audit(unit, run, "transformation.started", {})
                records = unit.raw_imports.list_by_import_sessions(import_session_ids)
                run.checked_records = len(records)
                context_records = unit.raw_imports.list_by_import_sessions(
                    context_import_session_ids
                )
                ledger_groups = self._ledger_groups((*records, *context_records))
                grouped_ids = {
                    record.id
                    for group in ledger_groups.values()
                    if len(group) > 1
                    for record in group
                }
                for record in records:
                    if record.id in grouped_ids:
                        self._transform_ledger_group_member(
                            unit, run, record, ledger_groups, counters, problems
                        )
                    elif record.source == "kraken-ledgers":
                        self._transform_ledger(unit, run, record, counters, problems)
                    elif record.source == "kraken-trades":
                        self._transform_trade(unit, run, record, counters, problems)
                    else:
                        self._decision(
                            unit,
                            run,
                            record,
                            DecisionType.UNSUPPORTED,
                            "source_unsupported",
                            (
                                "The raw source is outside the Kraken "
                                "transformation contract."
                            ),
                        )
                run.created_objects = counters.created
                run.internal_movements = counters.internal
                run.review_cases = counters.reviews
                run.error_count = counters.conflicts
                target = (
                    TransformationStatus.COMPLETED_WITH_REVIEW
                    if counters.reviews or counters.conflicts
                    else TransformationStatus.COMPLETED
                )
                transition_transformation(run, target, self._clock())
                self._audit(
                    unit,
                    run,
                    (
                        "transformation.completed_with_review"
                        if target is TransformationStatus.COMPLETED_WITH_REVIEW
                        else "transformation.completed"
                    ),
                    {"checked_records": len(records)},
                )
                unit.commit()
        except Exception as error:
            self._record_failed_run(run, error)
            return TransformationResult(
                run_id=run.id,
                status=TransformationStatus.FAILED,
                checked_records=run.checked_records,
                rewards=0,
                acquisitions=0,
                disposals=0,
                trade_executions=0,
                fee_events=0,
                internal_movements=0,
                review_cases=0,
                conflicts=0,
                valuation_requirements=0,
                reused_objects=0,
                problems=(
                    TransformationProblem(
                        code="transformation_persistence_error",
                        message="The transformation transaction failed.",
                        kind=TransformationProblemKind.INFRASTRUCTURE,
                    ),
                ),
            )
        return TransformationResult(
            run_id=run.id,
            status=run.status,
            checked_records=run.checked_records,
            rewards=counters.rewards,
            acquisitions=counters.acquisitions,
            disposals=counters.disposals,
            trade_executions=counters.trades,
            fee_events=counters.fees,
            internal_movements=counters.internal,
            review_cases=counters.reviews,
            conflicts=counters.conflicts,
            valuation_requirements=counters.valuations,
            reused_objects=counters.reused,
            problems=tuple(problems),
        )

    def _record_failed_run(self, run: TransformationRun, error: Exception) -> None:
        if run.status in {
            TransformationStatus.CREATED,
            TransformationStatus.PROCESSING,
        }:
            transition_transformation(run, TransformationStatus.FAILED, self._clock())
        else:
            run.status = TransformationStatus.FAILED
            run.completed_at = self._clock()
        run.error_count = 1
        run.error_summary = f"{type(error).__name__}: transformation transaction failed"
        try:
            with self._unit_of_work_factory() as recovery:
                recovery.transformation_runs.add(run)
                self._audit(
                    recovery,
                    run,
                    "transformation.failed",
                    {"error_type": type(error).__name__},
                )
                recovery.commit()
        except Exception:
            return

    @staticmethod
    def _ledger_groups(
        records: Sequence[RawImportRecord],
    ) -> dict[str, list[RawImportRecord]]:
        groups: dict[str, list[RawImportRecord]] = defaultdict(list)
        for record in records:
            values = _values(record)
            reference = values.get("refid", "")
            if (
                record.source == "kraken-ledgers"
                and values.get("type", "").lower() in {"spend", "receive"}
                and reference
            ):
                groups[reference].append(record)
        return groups

    def _transform_ledger_group_member(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        groups: dict[str, list[RawImportRecord]],
        counters: _Counters,
        problems: list[TransformationProblem],
    ) -> None:
        values = _values(record)
        group = groups[values["refid"]]
        kinds = {(_values(item).get("type", "").lower()) for item in group}
        if len(group) != 2 or kinds != {"spend", "receive"}:
            self._review(
                unit,
                run,
                record,
                counters,
                problems,
                "ledger_group_ambiguous",
                conflict=True,
            )
            return
        event_kind = (
            AcquisitionType.TRADE_BUY if values["type"].lower() == "receive" else None
        )
        if event_kind is None:
            self._decision(
                unit,
                run,
                record,
                DecisionType.DOMAIN_EVENT_CREATED,
                "ledger_group_disposal",
                "Ledger spend is linked to the grouped instant exchange.",
            )
            return
        asset = _record_asset(record, values["asset"], run.contract_version)
        if asset.canonical_code is None:
            self._review(unit, run, record, counters, problems, "asset_alias_unknown")
            return
        amount = abs(Decimal(values["amount"]))
        stable = self._stable(record, "ledger-acquisition", run.contract_version)
        acquisition = AcquisitionLot(
            stable_key=stable,
            payload_hash=record.content_hash,
            asset_raw_code=asset.raw_code,
            asset_code=asset.canonical_code,
            asset_mapping_version=asset.mapping_version,
            quantity=amount,
            occurred_at=_timestamp(values["time"]),
            acquisition_type=event_kind,
            provider=PROVIDER,
            account_scope=ACCOUNT_SCOPE,
            wallet_scope=WALLET_SCOPE,
            external_id=record.external_id or str(record.id),
            transformation_version=run.contract_version,
            valuation_status=ValuationStatus.VALUATION_REQUIRED,
            tax_treatment_hint=TaxTreatmentHint.TRADE_ACQUISITION,
        )
        if self._persist_projection(
            unit, run, record, acquisition, "AcquisitionLot", counters, problems
        ):
            for related in group:
                if related.id != record.id:
                    self._provenance(
                        unit, run, related, "AcquisitionLot", acquisition.id
                    )
            counters.acquisitions += 1
            self._valuation(unit, run, acquisition, "instant_exchange", counters)
            self._audit(
                unit,
                run,
                "transformation.acquisition_created",
                {"acquisition_id": str(acquisition.id)},
            )

    def _transform_ledger(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        counters: _Counters,
        problems: list[TransformationProblem],
    ) -> None:
        values = _values(record)
        kind = values.get("type", "").lower()
        subtype = values.get("subtype", "").lower()
        if subtype in INTERNAL_SUBTYPES or kind in INTERNAL_SUBTYPES:
            counters.internal += 1
            self._decision(
                unit,
                run,
                record,
                DecisionType.INTERNAL_MOVEMENT,
                f"internal_{subtype or kind}",
                "Kraken Earn/Staking allocation is an internal movement.",
            )
            self._audit(
                unit,
                run,
                "transformation.internal_movement",
                {"raw_id": str(record.id)},
            )
            return
        amount = Decimal(values.get("amount", "0"))
        reward = kind == "earn" and subtype == "reward"
        legacy = kind == "staking" and not subtype
        if reward or legacy:
            if amount <= 0:
                self._review(
                    unit,
                    run,
                    record,
                    counters,
                    problems,
                    "reward_amount_not_positive",
                )
                return
            self._reward(unit, run, record, values, amount, legacy, counters, problems)
            return
        if kind in {"earn", "staking"}:
            self._review(
                unit,
                run,
                record,
                counters,
                problems,
                "earn_staking_classification_unknown",
            )
            return
        if kind in {"spend", "receive"}:
            self._review(
                unit,
                run,
                record,
                counters,
                problems,
                "ledger_exchange_reference_missing",
            )
            return
        if kind in FINANCIAL_LEDGER_TYPES_REQUIRING_REVIEW:
            self._review(
                unit,
                run,
                record,
                counters,
                problems,
                f"ledger_{kind}_requires_review",
                message=(
                    "Provider record is financially relevant but cannot be mapped "
                    "conservatively without additional context."
                ),
            )
            return
        self._decision(
            unit,
            run,
            record,
            DecisionType.UNSUPPORTED,
            "ledger_type_unsupported",
            "The ledger type has no conservative Sprint 2D mapping.",
        )

    def _reward(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        values: dict[str, str],
        amount: Decimal,
        legacy: bool,
        counters: _Counters,
        problems: list[TransformationProblem],
    ) -> None:
        asset = _record_asset(record, values["asset"], run.contract_version)
        if asset.canonical_code is None:
            self._review(unit, run, record, counters, problems, "asset_alias_unknown")
            return
        fee = Decimal(values.get("fee", "0"))
        if fee < 0 or fee >= amount:
            self._review(unit, run, record, counters, problems, "reward_fee_invalid")
            return
        stable = self._stable(record, "reward", run.contract_version)
        acquisition = AcquisitionLot(
            stable_key=stable,
            payload_hash=record.content_hash,
            asset_raw_code=asset.raw_code,
            asset_code=asset.canonical_code,
            asset_mapping_version=asset.mapping_version,
            quantity=amount - fee,
            gross_quantity=amount,
            fee_quantity=fee,
            fee_asset=asset.canonical_code if fee else None,
            occurred_at=_timestamp(values["time"]),
            acquisition_type=(
                AcquisitionType.LEGACY_STAKING_REWARD
                if legacy
                else AcquisitionType.STAKING_REWARD
            ),
            provider=PROVIDER,
            account_scope=ACCOUNT_SCOPE,
            wallet_scope=WALLET_SCOPE,
            external_id=record.external_id or str(record.id),
            transformation_version=run.contract_version,
            valuation_status=ValuationStatus.VALUATION_REQUIRED,
            tax_treatment_hint=(
                TaxTreatmentHint.LEGACY_STAKING_REWARD
                if legacy
                else TaxTreatmentHint.PASSIVE_STAKING_REWARD
            ),
        )
        if self._persist_projection(
            unit, run, record, acquisition, "AcquisitionLot", counters, problems
        ):
            counters.rewards += 1
            counters.acquisitions += 1
            self._valuation(unit, run, acquisition, "reward_inflow", counters)
            self._audit(
                unit,
                run,
                "transformation.reward_created",
                {"acquisition_id": str(acquisition.id)},
            )

    def _transform_trade(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        counters: _Counters,
        problems: list[TransformationProblem],
    ) -> None:
        values = _values(record)
        pair = resolve_pair(values.get("pair", ""))
        if pair is None:
            self._review(unit, run, record, counters, problems, "trade_pair_unresolved")
            return
        side = values.get("type", "").lower()
        if side not in {"buy", "sell"}:
            self._review(
                unit, run, record, counters, problems, "trade_side_unsupported"
            )
            return
        price = Decimal(values["price"])
        cost = Decimal(values["cost"])
        volume = Decimal(values["vol"])
        fee = Decimal(values["fee"])
        if min(price, cost, volume) <= 0 or fee < 0:
            self._review(unit, run, record, counters, problems, "trade_amount_invalid")
            return
        if abs(price * volume - cost) > COST_TOLERANCE:
            self._review(unit, run, record, counters, problems, "trade_cost_mismatch")
            return
        references = tuple(
            item.strip()
            for item in values.get("ledgers", "").split(",")
            if item.strip()
        )
        reconciliation = self._reconciliation(
            unit,
            references,
            {
                pair.base.canonical_code or pair.base.raw_code,
                pair.quote.canonical_code or pair.quote.raw_code,
            },
            run.contract_version,
        )
        if reconciliation is ReconciliationStatus.CONFLICT:
            self._review(
                unit,
                run,
                record,
                counters,
                problems,
                "trade_ledger_asset_conflict",
                conflict=True,
            )
            return
        stable = self._stable(record, "trade", run.contract_version)
        trade = TradeExecution(
            stable_key=stable,
            payload_hash=record.content_hash,
            external_id=record.external_id or str(record.id),
            order_external_id=values["ordertxid"],
            raw_pair=values["pair"],
            base_asset_raw=pair.base.raw_code,
            base_asset=pair.base.canonical_code or pair.base.raw_code,
            quote_asset_raw=pair.quote.raw_code,
            quote_asset=pair.quote.canonical_code or pair.quote.raw_code,
            side=side,
            order_type=values["ordertype"],
            occurred_at=_timestamp(values["time"]),
            volume=volume,
            price=price,
            cost=cost,
            fee=fee,
            fee_asset=pair.quote.canonical_code,
            provider=PROVIDER,
            transformation_version=run.contract_version,
            reconciliation_status=reconciliation,
        )
        if not self._persist_projection(
            unit, run, record, trade, "TradeExecution", counters, problems
        ):
            return
        counters.trades += 1
        received_asset = pair.base if side == "buy" else pair.quote
        paid_asset = pair.quote if side == "buy" else pair.base
        received_quantity = volume if side == "buy" else cost
        paid_quantity = cost if side == "buy" else volume
        direct_eur = (
            paid_asset.canonical_code == "EUR"
            if side == "buy"
            else (received_asset.canonical_code == "EUR")
        )
        acquisition = AcquisitionLot(
            stable_key=self._stable(record, "acquisition", run.contract_version),
            payload_hash=record.content_hash,
            asset_raw_code=received_asset.raw_code,
            asset_code=received_asset.canonical_code or received_asset.raw_code,
            asset_mapping_version=ASSET_MAPPING_VERSION,
            quantity=received_quantity,
            occurred_at=trade.occurred_at,
            acquisition_type=(
                AcquisitionType.TRADE_BUY
                if paid_asset.canonical_code in FIAT_ASSETS
                else AcquisitionType.CRYPTO_EXCHANGE
            ),
            provider=PROVIDER,
            account_scope=ACCOUNT_SCOPE,
            wallet_scope=WALLET_SCOPE,
            external_id=trade.external_id,
            transformation_version=run.contract_version,
            valuation_status=(
                ValuationStatus.NATIVE_EUR_AVAILABLE
                if direct_eur
                else ValuationStatus.VALUATION_REQUIRED
            ),
            tax_treatment_hint=TaxTreatmentHint.TRADE_ACQUISITION,
            native_consideration_asset=paid_asset.canonical_code,
            native_consideration_quantity=paid_quantity,
        )
        unit.acquisitions.add(acquisition)
        self._provenance(unit, run, record, "AcquisitionLot", acquisition.id)
        counters.acquisitions += 1
        self._audit(
            unit,
            run,
            "transformation.acquisition_created",
            {"acquisition_id": str(acquisition.id)},
        )
        if direct_eur:
            self._valuation(
                unit,
                run,
                acquisition,
                "native_eur_trade_acquisition",
                counters,
                method=ValuationMethod.DIRECT_EUR,
            )
        else:
            self._valuation(unit, run, acquisition, "trade_acquisition", counters)
        if paid_asset.canonical_code not in FIAT_ASSETS:
            disposal = DisposalEvent(
                stable_key=self._stable(record, "disposal", run.contract_version),
                payload_hash=record.content_hash,
                asset_raw_code=paid_asset.raw_code,
                asset_code=paid_asset.canonical_code or paid_asset.raw_code,
                asset_mapping_version=ASSET_MAPPING_VERSION,
                quantity=paid_quantity,
                occurred_at=trade.occurred_at,
                disposal_type=DisposalType.CRYPTO_EXCHANGE,
                provider=PROVIDER,
                account_scope=ACCOUNT_SCOPE,
                wallet_scope=WALLET_SCOPE,
                external_id=trade.external_id,
                transformation_version=run.contract_version,
                valuation_status=(
                    ValuationStatus.NATIVE_EUR_AVAILABLE
                    if received_asset.canonical_code == "EUR"
                    else ValuationStatus.VALUATION_REQUIRED
                ),
                tax_treatment_hint=TaxTreatmentHint.CRYPTO_ASSET_EXCHANGE,
                native_consideration_asset=received_asset.canonical_code,
                native_consideration_quantity=received_quantity,
                trade_execution_id=trade.id,
            )
            unit.disposals.add(disposal)
            self._provenance(unit, run, record, "DisposalEvent", disposal.id)
            counters.disposals += 1
            self._audit(
                unit,
                run,
                "transformation.disposal_created",
                {"disposal_id": str(disposal.id)},
            )
            if disposal.valuation_status is ValuationStatus.NATIVE_EUR_AVAILABLE:
                self._valuation(
                    unit,
                    run,
                    disposal,
                    "native_eur_trade_disposal",
                    counters,
                    method=ValuationMethod.DIRECT_EUR,
                )
            else:
                self._valuation(unit, run, disposal, "trade_disposal", counters)
        if fee > 0:
            fee_event = FeeEvent(
                stable_key=self._stable(record, "fee", run.contract_version),
                payload_hash=record.content_hash,
                asset_code=pair.quote.canonical_code or pair.quote.raw_code,
                quantity=fee,
                occurred_at=trade.occurred_at,
                provider=PROVIDER,
                external_id=trade.external_id,
                transformation_version=run.contract_version,
                valuation_status=(
                    ValuationStatus.NATIVE_EUR_AVAILABLE
                    if pair.quote.canonical_code == "EUR"
                    else ValuationStatus.VALUATION_REQUIRED
                ),
                related_object_id=trade.id,
            )
            unit.fee_events.add(fee_event)
            self._provenance(unit, run, record, "FeeEvent", fee_event.id)
            counters.fees += 1
            self._audit(
                unit,
                run,
                "transformation.fee_created",
                {"fee_id": str(fee_event.id)},
            )
            if fee_event.valuation_status is ValuationStatus.NATIVE_EUR_AVAILABLE:
                self._valuation(
                    unit,
                    run,
                    fee_event,
                    "native_eur_trade_fee",
                    counters,
                    method=ValuationMethod.DIRECT_EUR,
                )
            else:
                self._valuation(unit, run, fee_event, "trade_fee", counters)
        self._audit(
            unit, run, "transformation.trade_created", {"trade_id": str(trade.id)}
        )

    @staticmethod
    def _reconciliation(
        unit: UnitOfWork,
        references: tuple[str, ...],
        pair_assets: set[str],
        contract_version: str,
    ) -> ReconciliationStatus:
        if not references:
            return ReconciliationStatus.NOT_REQUIRED
        found = 0
        for reference in references:
            records = unit.raw_imports.list_by_external_id(f"kraken:ledger:{reference}")
            found += bool(records)
            for record in records:
                raw_asset = _values(record).get("asset", "")
                asset = _record_asset(record, raw_asset, contract_version)
                if asset.canonical_code not in pair_assets:
                    return ReconciliationStatus.CONFLICT
        if found == len(references):
            return ReconciliationStatus.MATCHED
        return ReconciliationStatus.PARTIAL if found else ReconciliationStatus.PENDING

    def _persist_projection(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        entity: AcquisitionLot | TradeExecution,
        entity_type: str,
        counters: _Counters,
        problems: list[TransformationProblem],
    ) -> bool:
        unit.flush()
        existing: AcquisitionLot | TradeExecution | None
        if isinstance(entity, AcquisitionLot):
            existing = unit.acquisitions.find_by_stable_key(entity.stable_key)
        else:
            existing = unit.trade_executions.find_by_stable_key(entity.stable_key)
        if existing is not None:
            if existing.payload_hash == entity.payload_hash:
                self._decision(
                    unit,
                    run,
                    record,
                    DecisionType.DOMAIN_EVENT_REUSED,
                    "domain_event_reused",
                    (
                        "The same external record already has an identical "
                        "domain projection."
                    ),
                    existing.id,
                )
                counters.reused += 1
                self._audit(
                    unit,
                    run,
                    "transformation.duplicate_detected",
                    {"existing_id": str(existing.id)},
                )
            else:
                self._review(
                    unit,
                    run,
                    record,
                    counters,
                    problems,
                    "external_id_payload_conflict",
                    conflict=True,
                )
            return False
        if isinstance(entity, AcquisitionLot):
            unit.acquisitions.add(entity)
        else:
            unit.trade_executions.add(entity)
        self._provenance(unit, run, record, entity_type, entity.id)
        self._decision(
            unit,
            run,
            record,
            DecisionType.DOMAIN_EVENT_CREATED,
            f"{entity_type.lower()}_created",
            f"A provider-neutral {entity_type} was created.",
            entity.id,
        )
        return True

    def _review(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        counters: _Counters,
        problems: list[TransformationProblem],
        code: str,
        *,
        conflict: bool = False,
        message: str = "The raw record requires a conservative manual review.",
    ) -> None:
        kind = (
            TransformationProblemKind.CONFLICT
            if conflict
            else TransformationProblemKind.REVIEW
        )
        counters.conflicts += int(conflict)
        counters.reviews += 1
        unit.transformation_issues.add(
            TransformationIssue(
                transformation_run_id=run.id,
                raw_import_record_id=record.id,
                code=code,
                message=message,
                is_conflict=conflict,
                occurred_at=self._clock(),
            )
        )
        self._decision(
            unit,
            run,
            record,
            DecisionType.CONFLICT if conflict else DecisionType.REVIEW_REQUIRED,
            code,
            message,
        )
        problems.append(
            TransformationProblem(
                code=code,
                message=message,
                kind=kind,
                raw_import_record_id=record.id,
            )
        )
        if conflict:
            self._audit(
                unit,
                run,
                "transformation.conflict_detected",
                {"raw_id": str(record.id), "code": code},
            )

    def _valuation(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        entity: AcquisitionLot | DisposalEvent | FeeEvent,
        reason: str,
        counters: _Counters,
        *,
        method: ValuationMethod = ValuationMethod.DAILY_AVERAGE,
    ) -> None:
        unit.valuation_requirements.add(
            ValuationRequirement(
                asset_code=entity.asset_code,
                target_currency="EUR",
                valuation_at=entity.occurred_at,
                method=method,
                status=ValuationStatus.VALUATION_REQUIRED,
                reason_code=reason,
                domain_object_type=type(entity).__name__,
                domain_object_id=entity.id,
                transformation_run_id=run.id,
            )
        )
        counters.valuations += 1

    @staticmethod
    def _stable(record: RawImportRecord, event_type: str, contract_version: str) -> str:
        del contract_version
        external = record.external_id or f"raw:{record.id}"
        return f"{PROVIDER}|{external}|{event_type}|{DOMAIN_IDENTITY_VERSION}"

    @staticmethod
    def _provenance(
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        entity_type: str,
        entity_id: UUID,
    ) -> None:
        unit.domain_provenance.add(
            DomainProvenance(
                domain_object_type=entity_type,
                domain_object_id=entity_id,
                raw_import_record_id=record.id,
                import_session_id=record.import_session_id,
                transformation_run_id=run.id,
            )
        )

    def _decision(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        record: RawImportRecord,
        decision_type: DecisionType,
        reason: str,
        explanation: str,
        domain_object_id: UUID | None = None,
    ) -> None:
        unit.transformation_decisions.add(
            TransformationDecision(
                raw_import_record_id=record.id,
                import_session_id=record.import_session_id,
                transformation_run_id=run.id,
                contract_version=run.contract_version,
                decision_type=decision_type,
                reason_code=reason,
                explanation=explanation,
                domain_object_id=domain_object_id,
                decided_at=self._clock(),
            )
        )

    def _audit(
        self,
        unit: UnitOfWork,
        run: TransformationRun,
        event_type: str,
        metadata: dict[str, Any],
    ) -> None:
        unit.audit.add(
            AuditEvent(
                occurred_at=self._clock(),
                event_type=event_type,
                entity_type="TransformationRun",
                entity_id=run.id,
                actor_type=AuditActorType.SYSTEM,
                actor_id=run.actor_id,
                metadata={"contract_version": run.contract_version, **metadata},
            )
        )


def _values(record: RawImportRecord) -> dict[str, str]:
    return {
        str(key).strip().lower(): str(value) for key, value in record.payload.items()
    }


def _record_asset(
    record: RawImportRecord, raw_asset: str, contract_version: str
) -> AssetIdentity:
    if contract_version == DOMAIN_IDENTITY_VERSION:
        return resolve_asset_legacy_v1(raw_asset)
    canonical = record.technical_metadata.get("canonical_asset")
    mapping_version = record.technical_metadata.get("asset_mapping_version")
    normalized_asset = (
        canonical.get("normalized_asset") if isinstance(canonical, dict) else None
    )
    if (
        isinstance(canonical, dict)
        and canonical.get("raw_asset") == raw_asset
        and isinstance(normalized_asset, str)
        and bool(normalized_asset)
        and canonical.get("is_unambiguous") is True
        and isinstance(mapping_version, str)
        and bool(mapping_version)
    ):
        return AssetIdentity(
            raw_code=raw_asset,
            canonical_code=normalized_asset,
            mapping_version=mapping_version,
            mapping_status=MappingStatus.MAPPED,
        )
    return resolve_asset(raw_asset)


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return (
        parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    )
