# Sprint 5A sale planner

The page **Verkauf planen** creates proposals only. It contains no order action
and makes no exchange request.

The planner supports an exact asset quantity, an approximate gross EUR target,
and the complete documented inventory. A manual simulation price is required.
The response labels this source as `MANUAL_SIMULATION`; it is never presented as
a guaranteed execution price.

FIFO allocation operates on immutable snapshots of the latest completed tax
inventory. Cost basis uses the latest resolved valuation decision and its net
acquisition value. Open staking-platform-fee decisions do not change that cost
basis. Allocation output includes timestamps, exact elapsed seconds and days,
and a calendar-anniversary status. It does not label a disposal tax-free or
taxable.

Every result includes the versioned hint
`de-bmf-crypto-2025-03-06-v1` and explains that tax treatment depends on the
individual facts. Open tax reviews produce a `PARTIAL` status and concrete
warnings. A pending delisting tax mapping blocks the documented disposed asset.
Unmapped proceeds are not invented as an acquisition lot; unrelated inventory
of the same proceeds asset remains usable. The pending proceeds quantity stays
excluded until a future explicit tax mapping establishes its provenance.

The documented tax inventory is not an exchange balance. Consequently,
`exchange_available_quantity` is null and every result warns
`EXCHANGE_BALANCE_NOT_RECONCILED`.

## API

`GET /api/sale-proposals/inventory` lists documented quantities available to the
planner.

`POST /api/sale-proposals/simulate` accepts an asset, one of the three modes, a
manual EUR reference price, and the applicable quantity or EUR target. An
optional estimated EUR fee can be supplied. The result always contains:

```json
{
  "dry_run": true,
  "order_created": false,
  "exchange_mutated": false,
  "tax_run_created": false
}
```

No sale proposal, order, disposal, allocation, journal entry, review decision,
or tax run is stored.
