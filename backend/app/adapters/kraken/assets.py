import re
from dataclasses import dataclass
from enum import StrEnum

from app.core.transformation import AssetIdentity, MappingStatus

ASSET_MAPPING_VERSION = "kraken-assets-v2"
LEGACY_ASSET_MAPPING_VERSION = "kraken-assets-v1"
LEGACY_ASSET_ALIASES = {
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
ASSET_ALIASES = {
    **LEGACY_ASSET_ALIASES,
    "ZGBP": "GBP",
    "ZCAD": "CAD",
    "ZJPY": "JPY",
}
FIAT_ASSETS = frozenset({"EUR", "USD", "GBP", "CAD", "JPY"})
_ASSET_BASE = re.compile(r"^[A-Z0-9]{1,32}$")
_PRODUCT_ASSET = re.compile(r"^(?P<body>[A-Z0-9]{1,40})\.(?P<variant>[SBFM])$")
_PRODUCT_MARKER = re.compile(r"^(?P<base>[A-Z][A-Z0-9]*?)(?P<marker>\d{1,8})$")


class KrakenAssetNormalizationKind(StrEnum):
    ALIAS = "alias"
    IDENTITY = "identity"
    PRODUCT_VARIANT = "product_variant"
    INVALID = "invalid"


@dataclass(frozen=True, kw_only=True)
class KrakenAssetIdentity:
    raw_asset: str
    normalized_asset: str | None
    alias_kind: KrakenAssetNormalizationKind
    product_marker: str | None
    product_variant: str | None
    mapping_version: str
    is_unambiguous: bool


def normalize_kraken_asset(raw_asset: str) -> KrakenAssetIdentity:
    value = raw_asset
    product_marker: str | None = None
    product_variant: str | None = None
    product = _PRODUCT_ASSET.fullmatch(value)
    if product is not None:
        value = product.group("body")
        product_variant = product.group("variant")
        marker = _PRODUCT_MARKER.fullmatch(value)
        if marker is not None:
            value = marker.group("base")
            product_marker = marker.group("marker")
    if _ASSET_BASE.fullmatch(value) is None:
        return KrakenAssetIdentity(
            raw_asset=raw_asset,
            normalized_asset=None,
            alias_kind=KrakenAssetNormalizationKind.INVALID,
            product_marker=product_marker,
            product_variant=product_variant,
            mapping_version=ASSET_MAPPING_VERSION,
            is_unambiguous=False,
        )
    normalized = ASSET_ALIASES.get(value, value)
    if product_variant is not None:
        kind = KrakenAssetNormalizationKind.PRODUCT_VARIANT
    elif normalized != value:
        kind = KrakenAssetNormalizationKind.ALIAS
    else:
        kind = KrakenAssetNormalizationKind.IDENTITY
    return KrakenAssetIdentity(
        raw_asset=raw_asset,
        normalized_asset=normalized,
        alias_kind=kind,
        product_marker=product_marker,
        product_variant=product_variant,
        mapping_version=ASSET_MAPPING_VERSION,
        is_unambiguous=True,
    )


def resolve_asset(raw_code: str) -> AssetIdentity:
    normalized = normalize_kraken_asset(raw_code)
    return AssetIdentity(
        raw_code=normalized.raw_asset,
        canonical_code=normalized.normalized_asset,
        mapping_version=normalized.mapping_version,
        mapping_status=(
            MappingStatus.MAPPED
            if normalized.is_unambiguous
            else MappingStatus.UNRESOLVED
        ),
        review_reason=None if normalized.is_unambiguous else "asset_alias_unknown",
    )


def resolve_asset_legacy_v1(raw_code: str) -> AssetIdentity:
    canonical = LEGACY_ASSET_ALIASES.get(raw_code)
    return AssetIdentity(
        raw_code=raw_code,
        canonical_code=canonical,
        mapping_version=LEGACY_ASSET_MAPPING_VERSION,
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
