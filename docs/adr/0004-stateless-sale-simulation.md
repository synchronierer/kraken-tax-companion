# ADR 0004: Stateless sale simulation

## Status

Accepted

## Context

Sprint 5A needs sale proposals with FIFO and tax-impact estimates. A proposal must
not become an exchange order, a disposal, or a tax calculation. The documented
tax inventory and an exchange balance are separate facts.

## Decision

Sale planning is a provider-neutral, pure domain calculation. The API takes an
explicit manual reference price and reads the most recent completed tax inventory
plus each acquisition's latest valuation decision. It copies those records into
immutable simulation inputs. No proposal entities are persisted.

The API never imports a Kraken client. It reports the exchange quantity as null,
marks execution prices as not guaranteed, and always returns the four explicit
dry-run safety flags. Pending financial tax mappings block the documented
disposed asset. Unmapped proceeds are not turned into inventory; unrelated lots
of the proceeds asset are not blocked. Other open reviews remain visible
warnings and do not globally block planning.

## Consequences

Repeated simulations do not mutate inventory or tax records. Current exchange
availability remains unknown until a separate reconciliation feature exists. A
future live price adapter can implement `ReferencePriceSource` without changing
the FIFO simulation or introducing exchange write permissions.
