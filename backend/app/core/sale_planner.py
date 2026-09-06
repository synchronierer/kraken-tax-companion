from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_DOWN, Decimal, localcontext
from enum import StrEnum
from typing import Final, Protocol, cast
from uuid import UUID

from app.core.entities import positive_decimal, required_text
from app.core.time import require_utc
from app.core.valuation import (
    exact_decimal_multiply,
    exact_decimal_subtract,
    exact_decimal_sum,
)

SALE_SIMULATION_VERSION: Final = "sale-simulation-fifo-v1"
TAX_HINT_VERSION: Final = "de-bmf-crypto-2025-03-06-v1"
PRICE_STALE_AFTER_SECONDS: Final = 300
TAX_NOTICE: Final = "Steuerliche Einordnung ist vom Einzelfall abhängig."
TAX_HINTS: Final = (
    "Passives Staking kann § 22 Nr. 3 EStG betreffen.",
    "Eine spätere Veräußerung kann § 23 EStG betreffen.",
    "FIFO kann nach dem BMF-Schreiben unter Voraussetzungen als "
    "walletbezogene Vereinfachung verwendet werden.",
    "Die Simulation trifft keine definitive Steuerentscheidung.",
)


class SaleMode(StrEnum):
    QUANTITY = "quantity"
    TARGET_EUR = "target_eur"
    ALL_AVAILABLE_INVENTORY = "all_available_inventory"


class HoldingPeriodStatus(StrEnum):
    WITHIN_ONE_YEAR = "WITHIN_ONE_YEAR"
    OVER_ONE_YEAR = "OVER_ONE_YEAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, kw_only=True)
class ReferencePrice:
    price_eur: Decimal
    source: str
    timestamp: datetime

    def __post_init__(self) -> None:
        positive_decimal(self.price_eur, "price_eur")
        object.__setattr__(self, "source", required_text(self.source, "source"))
        object.__setattr__(self, "timestamp", require_utc(self.timestamp))


class ReferencePriceSource(Protocol):
    def get(self, asset: str, *, now: datetime) -> ReferencePrice: ...


@dataclass(frozen=True, kw_only=True)
class FixedReferencePriceSource:
    price: ReferencePrice

    def get(self, asset: str, *, now: datetime) -> ReferencePrice:
        required_text(asset, "asset")
        require_utc(now)
        return self.price


@dataclass(frozen=True, kw_only=True)
class SaleInventoryLot:
    inventory_lot_id: UUID
    acquisition_lot_id: UUID
    valuation_decision_id: UUID
    asset: str
    remaining_quantity: Decimal
    remaining_cost_eur: Decimal
    acquired_at: datetime
    sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", required_text(self.asset, "asset").upper())
        positive_decimal(self.remaining_quantity, "remaining_quantity")
        positive_decimal(self.remaining_cost_eur, "remaining_cost_eur")
        object.__setattr__(self, "acquired_at", require_utc(self.acquired_at))
        if self.sequence < 0:
            raise ValueError("sequence must not be negative.")


@dataclass(frozen=True, kw_only=True)
class SaleProposalRequest:
    asset: str
    mode: SaleMode
    reference_price: ReferencePrice
    quantity: Decimal | None = None
    target_eur: Decimal | None = None
    estimated_fee_eur: Decimal | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "asset", required_text(self.asset, "asset").upper())
        if self.mode is SaleMode.QUANTITY:
            if self.quantity is None:
                raise ValueError("quantity is required for quantity mode.")
            positive_decimal(self.quantity, "quantity")
        elif self.quantity is not None:
            raise ValueError("quantity is only valid for quantity mode.")
        if self.mode is SaleMode.TARGET_EUR:
            if self.target_eur is None:
                raise ValueError("target_eur is required for target_eur mode.")
            positive_decimal(self.target_eur, "target_eur")
        elif self.target_eur is not None:
            raise ValueError("target_eur is only valid for target_eur mode.")
        if self.estimated_fee_eur is not None and self.estimated_fee_eur < 0:
            raise ValueError("estimated_fee_eur must not be negative.")


@dataclass(frozen=True, kw_only=True)
class SimulatedFifoAllocation:
    acquisition_lot_id: UUID
    inventory_lot_id: UUID
    valuation_decision_id: UUID
    acquisition_at: datetime
    hypothetical_disposed_at: datetime
    quantity: Decimal
    acquisition_cost_eur: Decimal
    simulated_proceeds_eur: Decimal
    simulated_fee_eur: Decimal | None
    simulated_gain_loss_eur: Decimal
    holding_seconds: int
    holding_days: Decimal
    reached_one_year: bool
    holding_period_status: HoldingPeriodStatus


@dataclass(frozen=True, kw_only=True)
class SaleSimulation:
    simulation_id: UUID
    simulation_version: str
    asset: str
    mode: SaleMode
    requested_quantity: Decimal | None
    target_eur: Decimal | None
    inventory_quantity: Decimal
    available_inventory_quantity: Decimal
    exchange_available_quantity: None
    proposed_quantity: Decimal
    reference_price_eur: Decimal
    price_source: str
    price_timestamp: datetime
    price_age_seconds: int
    execution_price_guaranteed: bool
    estimated_gross_proceeds_eur: Decimal
    estimated_fee_eur: Decimal | None
    estimated_net_proceeds_eur: Decimal
    acquisition_cost_eur: Decimal
    estimated_gain_loss_eur: Decimal
    earliest_acquired_at: datetime
    latest_acquired_at: datetime
    fifo_allocations: tuple[SimulatedFifoAllocation, ...]
    warnings: tuple[str, ...]
    blocked_reasons: tuple[str, ...]
    tax_data_status: str
    tax_hint_version: str
    tax_hints: tuple[str, ...]
    tax_notice: str
    calculated_at: datetime
    dry_run: bool = True
    order_created: bool = False
    exchange_mutated: bool = False
    tax_run_created: bool = False


class SaleProposalError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _divide(numerator: Decimal, denominator: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        return numerator / denominator


def _target_quantity(target_eur: Decimal, price_eur: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 80
        context.rounding = ROUND_DOWN
        return target_eur / price_eur


def _anniversary(acquired_at: datetime) -> datetime:
    try:
        return acquired_at.replace(year=acquired_at.year + 1)
    except ValueError:
        return acquired_at.replace(year=acquired_at.year + 1, day=28)


def _holding(acquired_at: datetime, disposed_at: datetime) -> tuple[int, Decimal, bool]:
    if acquired_at > disposed_at:
        raise SaleProposalError(
            "ACQUISITION_AFTER_SIMULATED_SALE",
            "Ein Erwerbslos liegt nach dem Simulationszeitpunkt.",
        )
    seconds = int((disposed_at - acquired_at).total_seconds())
    days = _divide(Decimal(seconds), Decimal(86400))
    return seconds, days, disposed_at >= _anniversary(acquired_at)


def simulate_sale(
    *,
    simulation_id: UUID,
    request: SaleProposalRequest,
    lots: list[SaleInventoryLot],
    now: datetime,
    tax_data_status: str,
    tax_warnings: tuple[str, ...] = (),
) -> SaleSimulation:
    calculated_at = require_utc(now)
    relevant = sorted(
        (lot for lot in lots if lot.asset == request.asset),
        key=lambda lot: (lot.acquired_at, lot.sequence, lot.acquisition_lot_id.hex),
    )
    inventory_quantity = exact_decimal_sum(
        tuple(lot.remaining_quantity for lot in relevant)
    )
    if inventory_quantity <= 0:
        raise SaleProposalError(
            "UNKNOWN_OR_EMPTY_ASSET", "Für das Asset ist kein FIFO-Bestand vorhanden."
        )
    if request.mode is SaleMode.ALL_AVAILABLE_INVENTORY:
        proposed_quantity = inventory_quantity
    elif request.mode is SaleMode.TARGET_EUR:
        proposed_quantity = _target_quantity(
            cast(Decimal, request.target_eur), request.reference_price.price_eur
        )
    else:
        proposed_quantity = cast(Decimal, request.quantity)
    if proposed_quantity > inventory_quantity:
        raise SaleProposalError(
            "INSUFFICIENT_FIFO_INVENTORY",
            "Die Verkaufsmenge übersteigt den dokumentierten FIFO-Bestand.",
        )
    gross = exact_decimal_multiply(proposed_quantity, request.reference_price.price_eur)
    fee = request.estimated_fee_eur
    if fee is not None and fee > gross:
        raise SaleProposalError(
            "FEE_EXCEEDS_PROCEEDS", "Die geschätzte Gebühr übersteigt den Erlös."
        )
    net = gross if fee is None else exact_decimal_subtract(gross, fee)
    remaining = proposed_quantity
    allocated_proceeds = Decimal("0")
    allocated_fees = Decimal("0")
    allocations: list[SimulatedFifoAllocation] = []
    for lot in relevant:
        if remaining == 0:
            break
        quantity = min(remaining, lot.remaining_quantity)
        last = quantity == remaining
        cost = (
            lot.remaining_cost_eur
            if quantity == lot.remaining_quantity
            else _divide(
                exact_decimal_multiply(lot.remaining_cost_eur, quantity),
                lot.remaining_quantity,
            )
        )
        proceeds = (
            exact_decimal_subtract(gross, allocated_proceeds)
            if last
            else _divide(exact_decimal_multiply(gross, quantity), proposed_quantity)
        )
        allocation_fee = None
        if fee is not None:
            allocation_fee = (
                exact_decimal_subtract(fee, allocated_fees)
                if last
                else _divide(exact_decimal_multiply(fee, quantity), proposed_quantity)
            )
        gain = exact_decimal_sum(
            (
                proceeds,
                cost.copy_negate(),
                *(
                    (allocation_fee.copy_negate(),)
                    if allocation_fee is not None
                    else ()
                ),
            )
        )
        seconds, days, reached = _holding(lot.acquired_at, calculated_at)
        allocations.append(
            SimulatedFifoAllocation(
                acquisition_lot_id=lot.acquisition_lot_id,
                inventory_lot_id=lot.inventory_lot_id,
                valuation_decision_id=lot.valuation_decision_id,
                acquisition_at=lot.acquired_at,
                hypothetical_disposed_at=calculated_at,
                quantity=quantity,
                acquisition_cost_eur=cost,
                simulated_proceeds_eur=proceeds,
                simulated_fee_eur=allocation_fee,
                simulated_gain_loss_eur=gain,
                holding_seconds=seconds,
                holding_days=days,
                reached_one_year=reached,
                holding_period_status=(
                    HoldingPeriodStatus.OVER_ONE_YEAR
                    if reached
                    else HoldingPeriodStatus.WITHIN_ONE_YEAR
                ),
            )
        )
        remaining = exact_decimal_subtract(remaining, quantity)
        allocated_proceeds = exact_decimal_sum((allocated_proceeds, proceeds))
        if allocation_fee is not None:
            allocated_fees = exact_decimal_sum((allocated_fees, allocation_fee))
    costs = exact_decimal_sum(tuple(item.acquisition_cost_eur for item in allocations))
    gains = exact_decimal_sum(
        tuple(item.simulated_gain_loss_eur for item in allocations)
    )
    price_age = max(
        0, int((calculated_at - request.reference_price.timestamp).total_seconds())
    )
    warnings = ["EXCHANGE_BALANCE_NOT_RECONCILED", *tax_warnings]
    if fee is None:
        warnings.append("ESTIMATED_FEE_UNKNOWN")
    if price_age > PRICE_STALE_AFTER_SECONDS:
        warnings.append("REFERENCE_PRICE_STALE")
    return SaleSimulation(
        simulation_id=simulation_id,
        simulation_version=SALE_SIMULATION_VERSION,
        asset=request.asset,
        mode=request.mode,
        requested_quantity=request.quantity,
        target_eur=request.target_eur,
        inventory_quantity=inventory_quantity,
        available_inventory_quantity=inventory_quantity,
        exchange_available_quantity=None,
        proposed_quantity=proposed_quantity,
        reference_price_eur=request.reference_price.price_eur,
        price_source=request.reference_price.source,
        price_timestamp=request.reference_price.timestamp,
        price_age_seconds=price_age,
        execution_price_guaranteed=False,
        estimated_gross_proceeds_eur=gross,
        estimated_fee_eur=fee,
        estimated_net_proceeds_eur=net,
        acquisition_cost_eur=costs,
        estimated_gain_loss_eur=gains,
        earliest_acquired_at=allocations[0].acquisition_at,
        latest_acquired_at=allocations[-1].acquisition_at,
        fifo_allocations=tuple(allocations),
        warnings=tuple(warnings),
        blocked_reasons=(),
        tax_data_status=tax_data_status,
        tax_hint_version=TAX_HINT_VERSION,
        tax_hints=TAX_HINTS,
        tax_notice=TAX_NOTICE,
        calculated_at=calculated_at,
    )
