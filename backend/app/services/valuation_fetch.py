"""Plan UTC windows and retain one evidence record per HTTP response."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.time import utc_now
from app.core.transformation import ValuationMethod, ValuationRequirement
from app.core.valuation import (
    DailyPrice,
    PriceMethod,
    PriceObservation,
    PriceProviderError,
    ProviderEvidence,
    evidence_hash,
)
from app.database.mappings import provider_evidence as provider_evidence_table
from app.infrastructure.coingecko import CoinGeckoProvider, HttpAttempt


@dataclass(frozen=True)
class AssetFetchPlan:
    asset: str
    required_days: tuple[date, ...]
    existing_prices: tuple[DailyPrice, ...]
    missing_days: tuple[date, ...]
    windows: tuple[tuple[datetime, datetime], ...]


@dataclass(frozen=True)
class ObservationBatch:
    observations: tuple[PriceObservation, ...]
    evidence: ProviderEvidence


def plan_fetches(
    requirements: list[ValuationRequirement],
    prices: list[DailyPrice],
    provider: CoinGeckoProvider,
    *,
    refresh_prices: bool,
) -> tuple[AssetFetchPlan, ...]:
    days: dict[str, set[date]] = {}
    for requirement in requirements:
        if requirement.method != ValuationMethod.DIRECT_EUR:
            days.setdefault(requirement.asset_code, set()).add(
                requirement.valuation_at.astimezone(UTC).date()
            )
    plans = []
    for asset, required in sorted(days.items()):
        existing = []
        missing = []
        for day in sorted(required):
            candidates = [
                p for p in prices if p.asset_code == asset and p.price_date == day
            ]
            manual = [
                p for p in candidates if p.method == PriceMethod.MANUAL_DAILY_PRICE
            ]
            automatic = [
                p
                for p in candidates
                if p.method == PriceMethod.DAILY_AVERAGE_HOURLY
                and p.provider == provider.name
                and p.provider_contract_version == provider.contract_version
                and not refresh_prices
            ]
            eligible = manual or automatic
            if eligible:
                existing.append(max(eligible, key=lambda p: p.version))
            else:
                missing.append(day)
        windows = []
        if missing:
            cursor = datetime.combine(missing[0], time.min, UTC)
            end = datetime.combine(missing[-1] + timedelta(days=1), time.min, UTC)
            while cursor < end:
                following = min(cursor + timedelta(days=90), end)
                windows.append((cursor, following))
                cursor = following
        plans.append(
            AssetFetchPlan(
                asset,
                tuple(sorted(required)),
                tuple(existing),
                tuple(missing),
                tuple(windows),
            )
        )
    return tuple(plans)


def prefetch(
    plans: tuple[AssetFetchPlan, ...],
    provider: CoinGeckoProvider,
    db: Session,
    *,
    audit: Callable[[str, dict[str, object]], None] | None = None,
) -> dict[tuple[str, date], ObservationBatch | PriceProviderError]:
    result: dict[tuple[str, date], ObservationBatch | PriceProviderError] = {}
    for plan in plans:
        for start, end in plan.windows:
            days = [
                day for day in plan.missing_days if start.date() <= day < end.date()
            ]
            if audit is not None:
                audit(
                    "valuation.provider_fetch_started",
                    {
                        "asset": plan.asset,
                        "requested_from": start.isoformat(),
                        "requested_to": end.isoformat(),
                    },
                )
            provider.attempts.clear()
            failure = None
            observations: tuple[PriceObservation, ...] = ()
            try:
                fetched = provider.observations(plan.asset, "EUR", start, end)
                # Normalize inclusive provider endpoints to disjoint half-open windows.
                unique = {
                    o.observed_at: o for o in fetched if start <= o.observed_at < end
                }
                observations = tuple(unique[t] for t in sorted(unique))
            except PriceProviderError as error:
                failure = error
            attempts = provider.attempts
            if not attempts and failure is None:
                # Provider doubles implement the public observations contract only.
                attempts = [HttpAttempt(200, utc_now(), observations)]
            successful_evidence = None
            for attempt in attempts:
                response_hash = evidence_hash(attempt.observations)
                evidence = db.scalar(
                    select(ProviderEvidence).where(
                        provider_evidence_table.c.provider == provider.name,
                        provider_evidence_table.c.provider_contract_version
                        == provider.contract_version,
                        provider_evidence_table.c.provider_asset_id
                        == provider.asset_id(plan.asset),
                        provider_evidence_table.c.target_currency == "EUR",
                        provider_evidence_table.c.requested_from == start,
                        provider_evidence_table.c.requested_to == end,
                        provider_evidence_table.c.response_hash == response_hash,
                    )
                )
                if evidence is None:
                    evidence = ProviderEvidence(
                        provider=provider.name,
                        provider_contract_version=provider.contract_version,
                        provider_asset_id=provider.asset_id(plan.asset),
                        target_currency="EUR",
                        requested_from=start,
                        requested_to=end,
                        fetched_at=attempt.fetched_at,
                        http_status=attempt.http_status,
                        response_hash=response_hash,
                        observation_count=len(attempt.observations),
                        observations=[
                            {
                                "observed_at": o.observed_at.isoformat(),
                                "price_eur": str(o.price_eur),
                            }
                            for o in attempt.observations
                        ],
                        earliest_observed_at=min(
                            (o.observed_at for o in attempt.observations), default=None
                        ),
                        latest_observed_at=max(
                            (o.observed_at for o in attempt.observations), default=None
                        ),
                    )
                    db.add(evidence)
                    db.flush()
                successful_evidence = evidence
                if audit is not None:
                    audit(
                        "valuation.provider_evidence_stored",
                        {"evidence_id": str(evidence.id)},
                    )
            if failure is None and audit is not None:
                audit(
                    "valuation.provider_fetch_succeeded",
                    {
                        "asset": plan.asset,
                        "observations": len(observations),
                    },
                )
            for day in days:
                if failure is not None:
                    result[plan.asset, day] = failure
                else:
                    assert successful_evidence is not None
                    result[plan.asset, day] = ObservationBatch(
                        tuple(
                            o
                            for o in observations
                            if o.observed_at.astimezone(UTC).date() == day
                        ),
                        successful_evidence,
                    )
    return result
