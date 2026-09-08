# Sprint 5B.2 historical ledger transformation

Historical Kraken asset aliases are resolved through the deterministic local
registry. `ETH2` and product variants such as `ETH2.S` normalize to `ETH`.
There is no general prefix or numeric-suffix removal.

## Instant exchange groups

Kraken ledger `spend` and `receive` records are known event types. A group is
projected only when one non-empty provider reference identifies exactly two
records, with one negative spend, one positive receive, non-negative fees, and
unambiguous assets. Isolated, malformed, or ambiguous records require review.

The projections are provider-neutral:

- Fiat to crypto creates a crypto acquisition with native consideration. It
  does not create a fiat disposal.
- Crypto to fiat creates a crypto disposal with native consideration. It does
  not create a fiat acquisition.
- Crypto to crypto creates both a disposal and an acquisition with reciprocal
  native consideration and no invented EUR value.

Native EUR consideration uses `NATIVE_EUR_AVAILABLE` and `DIRECT_EUR`.
Otherwise valuation remains required. Positive fees are separate `FeeEvent`
objects in the asset of their originating ledger record; they are never folded
away or projected twice.

Stable keys derive from the provider ledger identities, while the projection
payload hash covers both records. Both raw records are linked as provenance to
each generated object. Exact reprocessing reuses projections; changed payloads
conflict instead of creating economic duplicates.

External crypto deposits remain review cases. They are not interpreted as new
tax acquisition dates.
