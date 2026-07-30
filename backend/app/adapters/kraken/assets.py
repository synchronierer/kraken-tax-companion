from dataclasses import dataclass

from app.core.transformation import AssetIdentity, MappingStatus

ASSET_MAPPING_VERSION = "kraken-assets-v1"
ASSET_ALIASES = {
    "BTC": "BTC",
    "XBT": "BTC",
    "XXBT": "BTC",
    "ETH": "ETH",
    "XETH": "ETH",
    "EUR": "EUR",
    "ZEUR": "EUR",
    "USD": "USD",
    "ZUSD": "USD",
}
FIAT_ASSETS = frozenset({"EUR", "USD"})


def resolve_asset(raw_code: str) -> AssetIdentity:
    canonical = ASSET_ALIASES.get(raw_code)
    return AssetIdentity(
        raw_code=raw_code,
        canonical_code=canonical,
        mapping_version=ASSET_MAPPING_VERSION,
        mapping_status=(
            MappingStatus.MAPPED if canonical is not None else MappingStatus.UNRESOLVED
        ),
        review_reason=None if canonical is not None else "asset_alias_unknown",
    )


@dataclass(frozen=True, kw_only=True)
class ResolvedPair:
    raw_pair: str
    base: AssetIdentity
    quote: AssetIdentity


def resolve_pair(raw_pair: str) -> ResolvedPair | None:
    if "/" in raw_pair:
        parts = raw_pair.split("/")
        if len(parts) != 2:
            return None
        base, quote = (resolve_asset(part) for part in parts)
        if base.canonical_code is None or quote.canonical_code is None:
            return None
        return ResolvedPair(raw_pair=raw_pair, base=base, quote=quote)
    candidates: list[ResolvedPair] = []
    aliases = sorted(ASSET_ALIASES, key=len, reverse=True)
    for base_raw in aliases:
        if not raw_pair.startswith(base_raw):
            continue
        quote_raw = raw_pair[len(base_raw) :]
        if quote_raw in ASSET_ALIASES:
            candidates.append(
                ResolvedPair(
                    raw_pair=raw_pair,
                    base=resolve_asset(base_raw),
                    quote=resolve_asset(quote_raw),
                )
            )
    unique = {
        (candidate.base.canonical_code, candidate.quote.canonical_code): candidate
        for candidate in candidates
    }
    return next(iter(unique.values())) if len(unique) == 1 else None
