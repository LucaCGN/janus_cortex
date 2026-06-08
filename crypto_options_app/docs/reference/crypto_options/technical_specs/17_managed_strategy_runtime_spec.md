# Managed Strategy Runtime Spec

Rotation8 proved that raw profile pressure can create fills, but unchanged hedge/scalp/grid mechanics are not enough. Rotation9 must use managed runtime mechanics rather than rerunning `hedger_ratio_replication_v3` and `profile_hedge_scalping_v3` unchanged.

## Signal Separation

Profile rows have two states:

- observation signal: useful for research, reporting, or profile-universe metrics
- executable signal: fresh, low-conflict, sufficient support, and safe to pass into strategy candidate generation

Executable profile pressure rejects:

- stale supporting signals
- high conflict ratio
- empty supporting signals
- row-level blockers
- insufficient support weight
- managed v4/grid entries with extreme executable quotes and meaningful conflict
- managed entries with less than 120 seconds remaining unless the strategy explicitly overrides that in a later reviewed spec

## Managed Mechanics

Managed strategies must model:

- BUY entry
- cashout SELL intent
- rebuy target after profitable cashout
- stale order review every 30-60 seconds
- buy-only hedge-ratio rebalance
- lifecycle coverage for every BUY path
- settlement or managed exit attribution

The manager emits intent plans only. It does not submit orders.

## Rotation9 Gate Finding

`signal-live-rotation9-managed-pricepath-20260604T062245Z` exposed a specific bad executable shape:

- same-symbol/same-outcome profile pressure carried into a current event
- support was large, but opposing pressure was also large
- the executable quote was near an extreme, about 98c ask
- the strategy was a managed cashout/rebuy lane with weak remaining exit geometry

That shape is observation-only until replay proves it can be managed profitably. The runtime now blocks managed profile-derived entries when:

- conflict ratio is above 30%
- ask is 90c or higher and conflict ratio is above 20%
- cashout/rebuy entry ask is 92c or higher
- time remaining is under 120 seconds

These blockers apply to managed profile/grid lanes only. They do not change the global profile grading system.

## Rotation9 Managed Variants

- `hedger_ratio_replication_v4`: managed hedge-ratio replication with executable profile pressure and price-path context.
- `profile_hedge_scalping_v4`: managed profile hedge/scalp with cashout/rebuy and stale-order review.
- `grid_band_rebound_v3`: managed grid/band rebound using Up/Down price path and order-book pressure.

## Tests

- Profile-pressure gate rejects stale/high-conflict rows.
- BUY -> SELL/cashout -> rebuy plan is covered by pure unit tests.
- Hedge rebalance emits BUY-only rebalance plans.
- Stale order review becomes due after configured age.

## Safety

Managed runtime code must not bypass the supervised executor boundary, risk gates, lifecycle persistence, or reconciliation.
