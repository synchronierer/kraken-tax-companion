# Sprint 5B live context

The sale planner can explicitly load a read-only Kraken context for a selected
asset. The existing manual simulation remains available and unchanged.

## Balances

The private client calls only `POST /0/private/BalanceEx`. It calculates each
available balance with exact decimals:

```text
balance + credit - credit_used - hold_trade
```

Kraken aliases use the existing versioned asset normalization, including the
BTC/XBT aliases. Provider codes and raw balance components remain visible.
Unknown codes produce a warning. Product extensions `.B`, `.F`, `.S`, and `.M`
are reported as non-spot balances and are never added to immediately available
spot inventory.

Balance permission is diagnosed separately from authentication and existing
ledger permission. A missing Funds/Query permission does not prevent the public
market quote from loading and does not request trading or withdrawal rights.

## Market quote and pair resolution

The unauthenticated public client reads `AssetPairs` and resolves a direct EUR
pair using Kraken base and quote metadata. It does not construct a pair name by
concatenation and does not invent cross-rates. If no direct EUR pair exists, the
price stays unavailable with `KRAKEN_EUR_PAIR_UNAVAILABLE`.

The ticker retains best bid, best ask, and last trade separately. Simulations
use `KRAKEN_BEST_BID`; the response always states
`execution_price_guaranteed=false` and includes the fetch time and price age.

## Reconciliation and simulation

The context compares current stateless FIFO inventory with the Kraken total
balance as `MATCH`, `DIFFERENCE`, or `UNKNOWN`. A difference is informational;
it creates no lot, disposal, correction, or other persistence.

A live simulation requires both a readable balance and a direct EUR quote. Its
maximum quantity is the lower of documented FIFO inventory and available spot
balance. Non-spot extensions cannot increase this maximum. All responses remain
dry runs: no order, exchange mutation, tax run, review decision, or tax artifact
is created.
