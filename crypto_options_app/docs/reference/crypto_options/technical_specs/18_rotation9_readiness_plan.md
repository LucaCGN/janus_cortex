# Rotation9 Readiness Plan

Rotation9 is blocked until the centralized app and price-path hardening gates pass.

Current evidence from `signal-live-rotation9-managed-pricepath-20260604T062245Z`:

- The run stopped on `submit_error_no_fill`.
- Successful BUY rows matched the latest order-integrity audit.
- The run did not reach the 10 global filled event-slug target.
- `grid_band_rebound_v3` and the v4 managed lanes did not produce enough executable evidence.
- Price-path capture exists, but trade prints were not captured yet.
- The `submit_error_no_fill` row came from a managed profile lane accepting a near-extreme entry quote with meaningful opposing profile pressure. That is now treated as an observation-only signal shape.

## Required Pre-Run Gates

- Centralization audit passes.
- Full `tests/crypto_options_app` suite passes.
- Price-path capture writes centralized DB rows and watermarks.
- Replay can simulate at least one Up/Down price path with contemporaneous quotes.
- Completed captured events have persisted `polymarket_event_path_stats`.
- Trade/live-activity capture writes `polymarket_trade_prints` or reports a clear data-quality blocker.
- Signal gates reject Rotation8-style stale/high-conflict profile rows.
- Managed signal gates reject high-conflict or extreme-price managed profile entries before executor submission.
- Managed runtime tests cover cashout/rebuy, hedge rebalance, and stale-order review.
- Health/dashboard expose centralized paths and data-service freshness.

## Rotation9 Candidate Pool

Primary lanes:

- `profile_hedge_scalping_v1`
- `event_context_profile_confirmed_v1`
- `hedger_ratio_replication_v4`
- `profile_hedge_scalping_v4`
- `grid_band_rebound_v3`

## Expected Purpose By Lane

| Lane | Purpose |
| --- | --- |
| `profile_hedge_scalping_v1` | Retain a known working baseline lane. |
| `event_context_profile_confirmed_v1` | Retain a profile-confirmed event-context baseline. |
| `hedger_ratio_replication_v4` | Validate managed hedge-ratio rebalance with executable profile pressure. |
| `profile_hedge_scalping_v4` | Validate cashout/rebuy and stale-order review mechanics. |
| `grid_band_rebound_v3` | Validate price-path and order-book depth-driven band/rebound logic. |

Do not rerun this exact pool until trade-print capture and replay-safe managed exits are fixed. The first `submit_error_no_fill` root cause has been narrowed to the managed-entry signal gate and patched, but it still needs a full live-free integrity pass plus one future supervised live validation before the lane can be called reliable.

## Current Data-Service Readiness

- `polymarket_updown_pair_snapshots` and `polymarket_event_path_stats` are available in the centralized DB.
- Event-path stats cover completed captured events and include range, volatility, level crossings, bucket counts, rebound touches, pair-sum drift, pair-depth pressure, latency, and trade-print count.
- REST live-activity capture can reach the sampled official endpoint, but sampled market payloads did not include trade arrays. Trade-print rows are therefore still a data-quality gap until websocket `activity.trades` / `activity.orders_matched` capture is implemented.

## Explicit Non-Goals

- Do not rerun unchanged `hedger_ratio_replication_v3` or `profile_hedge_scalping_v3` as if they were fixed.
- Do not manually place orders.
- Do not allow data services to authorize or route orders.

## GitHub Grounding

- Parent: #123.
- Centralization: #124.
- Specs: #125.
- Price capture: #126.
- DB extension: #127.
- Indicators/replay context: #128.
- Replay fill/exit: #129.
- Profile-pressure gates: #130.
- Managed runtime: #131.
- Rotation9 plan: #132.
- Observability: #133.
- Integrity review: #134.
