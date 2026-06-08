# Data Blocks A/C Readiness Checkpoint

Date: 2026-06-04

Status: validated checkpoint before Block B profile-distribution work.

## Summary

Data blocks A and C are now implemented in the centralized `crypto_options_app` folder with read-only services, canonical DB persistence, health readiness rows, and passing tests.

No A/C data service may authorize orders. All data services reject live execution flags:

- `JANUS_CRYPTO_OPTIONS_LIVE_EXECUTE`
- `JANUS_CRYPTO_OPTIONS_LIVE_APPROVED`
- `JANUS_CRYPTO_OPTIONS_ACK_LIVE_RISK`

## Running Services At Checkpoint

| Service | Process | Cadence | Role |
| --- | --- | --- | --- |
| API / dashboard | `crypto_options_app.api.app:create_app` on `127.0.0.1:8011` | live | `/health` and dashboard state |
| C Polymarket option path capture | `run_crypto_options_option_price_capture.py` | 5s | Up/Down quotes, depth, pair snapshots, event path stats |
| A IFCM technical observer | `run_crypto_options_underlying_technical_observers.py` | 30s | BTC/ETH external technical observer snapshots for `1m`, `5m`, `15m` |

TradersUnion is integrated and persisted as a slower complementary observer. It is not run at 30s cadence because the page is large and did not show fast refresh behavior during probing.

## Validation

Command:

```powershell
python -m pytest tests/crypto_options_app -q
```

Result:

```text
174 passed
```

## Current Readiness Rows

| Block | Module | BTC | ETH | Meaning |
| --- | --- | --- | --- | --- |
| A | `underlying_technical_observers` | ready | ready | IFCM `1m`, `5m`, `15m` technical observers fresh enough for 30s context |
| C | `polymarket_option_price_capture` | ready | ready | Paired Up/Down price snapshot fresh enough for 30s option-path context |

## Central DB Row Counts At Checkpoint

| Table | Rows |
| --- | ---: |
| `external_technical_observer_snapshots` | 44 |
| `external_technical_observer_components` | 856 |
| `data_signal_readiness_snapshots` | 80 |
| `polymarket_price_ticks` | 32,298 |
| `polymarket_order_book_levels` | 568,905 |
| `polymarket_updown_pair_snapshots` | 15,232 |
| `polymarket_event_path_stats` | 103 |

## A Indicator Capability

| Indicator/stat | Phase | Frames | Refresh | Example |
| --- | --- | --- | --- | --- |
| IFCM summary score | pre/live | `1m`, `5m`, `15m` | 30s | `BTC 5m Sell score=-3` |
| IFCM moving averages | pre/live | `1m` to `1w` | 30s fast / slower long | `EMA10=SELL` |
| IFCM oscillators | pre/live | `1m` to `1w` | 30s fast / slower long | `MACD=SELL` |
| IFCM pivots | pre/live | `1m` to `1w` | slower context | support/resistance bands |
| TradersUnion rich oscillators | pre/live | current page context | slow fallback | RSI, ATR, ADX, PPO, Bull/Bear |
| Local EMA momentum | pre/live | `1m` to `1h` | 30s when local ticks fresh | target-relative trend |
| Local volatility | pre/live | `5m`, `1h`, `1d` | 30s+ | event feasibility/regime |
| Local support/resistance | pre/live | `5m`, `15m`, `1h` | 30s+ | rebound/resistance proximity |

## C Indicator Capability

| Indicator/stat | Phase | Frames | Refresh | Example |
| --- | --- | --- | --- | --- |
| Up/Down best bid/ask | pre/live | current + 15m future | 5s now, supports 30s | executable quote |
| Paired Up/Down snapshot | pre/live | current + 15m future | 5s | `up_ref=0.025 down_ref=0.975` |
| Pair sum/spread/depth pressure | live | current event | 5s | mispricing/liquidity imbalance |
| Event price points | replay/live context | `0s`, `10s`, `30s`, `60s`... | accumulates | path evolution |
| Pre-event price points | pre | `-15m` to `-10s` | accumulates | buying-ahead drift |
| Swing/range/rebound metrics | replay/live context | `30s`, `60s`, full event | accumulates | grid/scalp opportunity |
| Level crossing counts | replay/live context | 10c buckets | accumulates | price-level behavior |

## Next Block

Block B must provide a predictable 30s `top_profiles_distribution` payload during each event window, using:

1. profile order/activity stream aggregation when available,
2. recent batch order/activity refresh when streaming is unavailable,
3. current-position snapshot fallback when order granularity is insufficient.

The output must be usable as both an outcome expectation signal and a hedge-proportion target.
