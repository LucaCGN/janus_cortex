# Profile Distribution Signal Service Spec

Date: 2026-06-04

This spec defines data block B for the centralized Crypto Options App: profile universe live tracking and the `top_profiles_distribution` signal.

The service is read-only. It must never place, authorize, cancel, sign, route, or recommend orders. It rejects live trading environment flags and writes only canonical DB facts, readiness rows, and watermarks.

## Objective

Produce a reliable Up/Down distribution for the best crypto options profiles every 30 seconds, especially from 0:30 to 4:30 of each 5-minute event.

Canonical signal:

```json
{
  "signal_id": "top_profiles_distribution",
  "event_slug": "btc-updown-5m-...",
  "symbol": "BTC",
  "phase": "live",
  "top_profiles_distribution": {"down": 0.18, "up": 0.82},
  "canonical_method": "cost_weighted",
  "source_mode": "hybrid",
  "profile_count": 18,
  "component_count": 25,
  "source_age_seconds": 12.4
}
```

The trading engine should treat this as a profile-derived outcome/hedge signal. It is not itself an authorization to trade.

## Official Polymarket Resource Map

| Resource | Useful For B | Decision |
|---|---|---|
| `Polymarket/real-time-data-client` | Public websocket topic `activity`, types `trades` and `orders_matched`; payload includes `proxyWallet`, `outcome`, `side`, `price`, `size`, `asset`, `conditionId`, `eventSlug`, and `timestamp`. | Best primary stream for live profile order aggregation when filtered by current/future event slug. |
| `Polymarket/polymarket-cli` | Confirms official data commands for wallet `positions`, `closed-positions`, `trades`, `activity`, and market `holders`. | Best reference for batch refresh and fallback endpoint semantics. |
| `Polymarket/polymarket-us-python` | Useful for market data, BBO/order book, authenticated own portfolio; not appropriate for following external profile positions unless available through public endpoints. | Keep for market data and our own account state, not as the main external-profile source. |
| `Polymarket/py-clob-client-v2` | Useful for CLOB market/order lifecycle, order construction, market activity endpoints, and execution constraints. | Keep for execution and CLOB integrity; not a profile universe source of truth. |

## Source Modes

The service must support all three methods and report which was used.

| Mode | Source Tables | External Feed | Strength | Weakness |
|---|---|---|---|---|
| `stream_orders` | `profile_raw_activity`, `profile_event_orders` | Real-time activity websocket rows with profile wallet fields. | Lowest latency, best for intra-event distribution changes. | Needs stable websocket connection and event filters. |
| `batch_activity` | `profile_raw_activity`, `profile_event_orders` | Wallet activity/trades fetched on demand. | Good catch-up path, simpler than websockets. | Higher latency and rate-limit pressure across large pools. |
| `position_snapshot_fallback` | `profile_event_positions` | Wallet/current position snapshots. | Most reliable fallback when activity granularity is missing. | Loses lot timing and cannot distinguish fast flips/scalps cleanly. |

The current implementation supports all three provenance paths in the canonical aggregator. External polling is optional and bounded; the default path reads the canonical DB so tests and health remain deterministic.

## Canonical Tables

### `profile_distribution_snapshots`

One row per computed event distribution.

Important columns:

- `event_key`, `event_slug`, `symbol`
- `phase`: `pre`, `live`, `post`, or `unknown`
- `source_mode`: `stream_orders`, `batch_activity`, `event_order_reconstruction`, `position_snapshot_fallback`, `hybrid`, or `no_profile_source`
- `canonical_method`: default `cost_weighted`
- `profile_count`, `component_count`
- weighted Up/Down totals for cost, shares, and profile count
- `distribution_json`: canonical distribution, all variants, phase detail, source age
- `blockers_json`

### `profile_distribution_components`

One row per profile/outcome contribution.

Important columns:

- `distribution_snapshot_key`
- `profile_key`, `handle`, `grade`, `trading_style`
- `source_mode`
- `outcome`
- `net_shares`, `net_notional_usd`, `cost_basis_usd`
- `grade_weight`, `style_weight`, `final_weight`
- `contribution_json`

## Calculation Variants

The service persists all variants on every snapshot:

| Variant | Formula | Intended Use |
|---|---|---|
| `cost_weighted` | Sum profile cost/notional by Up/Down after grade/style weights. | Default outcome/hedge signal because it reflects committed capital. |
| `shares_weighted` | Sum net shares by Up/Down after grade/style weights. | Useful when price differs sharply between sides and we care about payout exposure. |
| `profile_count_weighted` | Count supporting profiles by Up/Down after grade/style weights. | Useful as a consensus sanity check when a few profiles dominate capital. |

Grade weights:

- `S++`: `1.4`
- `S+`: `1.2`
- `S`: `1.0`
- `A`: available only if explicitly configured, default excluded.

Style weights:

- `outcome_predictor` / one-side buy-and-hold: `1.25`
- `hedger`: `1.0`
- `grid_buyer`: `0.95`
- `scalping_trader`: `0.35`
- `unknown`: `0.5`

These weights are data-service weights, not strategy sizing rules.

## Readiness

The service writes `data_signal_readiness_snapshots` with:

- `data_block = "B"`
- `module_id = "top_profiles_distribution"`
- `target_refresh_seconds = 30`

Readiness status:

| Status | Meaning |
|---|---|
| `ready` | At least one current/pre/live event has non-stale profile components and no blockers. |
| `degraded` | Events exist but distributions are missing, stale, or source coverage is weak. |
| `missing` | No relevant events were available. |

Critical blockers:

- `no_eligible_top_profiles`
- `no_relevant_crypto_events`
- `no_profile_distribution_components`
- `no_up_down_distribution_weight`
- `profile_distribution_source_stale`
- provider errors from external activity/position fetchers

## Runtime

Script:

```powershell
python crypto_options_app/scripts/run_crypto_options_profile_distribution_service.py --loop --interval-seconds 30 --symbols BTC ETH --max-profiles 120 --state-path crypto_options_app/artifacts/automation/profile_distribution_status.json --json
```

Optional external fetch mode:

```powershell
python crypto_options_app/scripts/run_crypto_options_profile_distribution_service.py --include-external-fetch --loop --interval-seconds 30
```

External fetch mode must remain read-only and bounded by `--max-concurrency`.

## Integration Rules

- Strategies consume only snapshot/readiness payloads, not raw monitor artifacts.
- Strategies must reject stale B snapshots according to their own risk gates.
- The data service does not decide order side or size.
- The trading engine may use the canonical distribution for hedge ratio targets or outcome bias only after risk and reconciliation gates pass.
- If B is degraded, strategies requiring profile distribution must block or use an explicit strategy-level fallback.

## Test Coverage

Current focused tests:

- grade/style weighted activity aggregation
- position snapshot fallback
- external fetcher persistence path
- readiness row emission
- live trading flag rejection

Command:

```powershell
python -m pytest tests/crypto_options_app/test_profile_distribution_service_pytest.py -q
```

## Current Known State

The centralized DB already contains profile grades and historical activity, but some profile rows may be stale relative to today’s live events. That is expected to produce degraded B readiness until the profile fetch/stream process is run against current/future event slugs.

The next implementation step is to keep the B service running beside A and C, then validate whether the external activity/position fetch paths can refresh enough top profiles every 30 seconds without rate-limit pressure. If not, reduce pool size or switch to snapshot fallback for guaranteed signal generation.
